# wand/drivers/moglabs_ddlc.py

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import re
import time
from typing import Callable, Optional, Tuple, Union

#from mogdevice import MOGDevice
from wand.drivers.mogdevice import MOGDevice

logger = logging.getLogger(__name__)

_NUMBER_RE = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)


class MOGDLCError(RuntimeError):
    """Raised when the MOGLabs dDLC adapter cannot complete an operation."""


class LockLost(RuntimeError):
    """Raised by the standalone lock loop when the lock should be dropped."""


async def _run_blocking(func, *args, **kwargs):
    """Run a blocking function without blocking the asyncio event loop."""
    loop = asyncio.get_running_loop()
    call = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(None, call)


def _parse_first_float(response: Union[str, bytes]) -> float:
    """Parse the first numeric value from a dDLC response such as '-32.82 %'."""
    if isinstance(response, bytes):
        response = response.decode(errors="replace")
    match = _NUMBER_RE.search(str(response))
    if not match:
        raise MOGDLCError(f"Could not parse numeric value from {response!r}")
    return float(match.group(0))


def _normalise_path(path: str) -> str:
    return path.strip().lower().replace("_", "-").lstrip(":")


class MoglabsDLCproCompat:
    """
    Async WAnD-compatible wrapper around MOGLabs MOGDevice.

    """

    def __init__(
        self,
        addr: str,
        port: Optional[int] = None,
        timeout: float = 1.0,
        *,
        piezo_full_scale_v: float = 150.0,
        set_span_zero_on_open: bool = False,
        check_connection: bool = True,
    ) -> None:
        self.addr = addr
        self.port = port
        self.timeout = timeout
        self.piezo_full_scale_v = float(piezo_full_scale_v)
        self.set_span_zero_on_open = bool(set_span_zero_on_open)
        self.check_connection = bool(check_connection)

        if self.piezo_full_scale_v <= 0:
            raise ValueError("piezo_full_scale_v must be positive")

        self._dev: Optional[MOGDevice] = None
        self._io_lock = asyncio.Lock()

    async def open(self) -> "MoglabsDLCproCompat":
        """Open the TCP/USB connection to the dDLC."""
        self._dev = await _run_blocking(
            MOGDevice,
            self.addr,
            self.port,
            self.timeout,
            self.check_connection,
        )

        if self.set_span_zero_on_open:
            # WAnD-style wavemeter locking expects a static actuator,
            # not a continuing scan.
            await self.set("SPAN", 0.0)

        return self

    async def close(self) -> None:
        """Close the dDLC connection."""
        if self._dev is not None:
            dev = self._dev
            self._dev = None
            await _run_blocking(dev.close)

    def _device(self) -> MOGDevice:
        if self._dev is None:
            raise MOGDLCError("MOGLabs dDLC is not open")
        return self._dev

    async def ask(self, command: str) -> str:
        """Async wrapper around MOGDevice.ask()."""
        async with self._io_lock:
            return await _run_blocking(self._device().ask, command)

    async def cmd(self, command: str) -> str:
        """Async wrapper around MOGDevice.cmd()."""
        async with self._io_lock:
            return await _run_blocking(self._device().cmd, command)

    async def ask_dict(self, command: str):
        """Async wrapper around MOGDevice.ask_dict()."""
        async with self._io_lock:
            return await _run_blocking(self._device().ask_dict, command)

    def _offset_percent_to_voltage(self, offset_percent: float) -> float:
        return (offset_percent + 100.0) * self.piezo_full_scale_v / 200.0

    def _voltage_to_offset_percent(self, voltage: float) -> float:
        return 200.0 * float(voltage) / self.piezo_full_scale_v - 100.0

    @staticmethod
    def _path_kind(path: str) -> str:
        p = _normalise_path(path)

        # WAnD/TOPTICA-compatible aliases for a piezo set voltage.
        if p in {
            "dl:pc:voltage-set",
            "laser1:dl:pc:voltage-set",
            "piezo:voltage-set",
            "laser1:piezo:voltage-set",
            "moglabs:piezo-voltage-v",
            "laser1:moglabs:piezo-voltage-v",
        }:
            return "piezo_voltage_v"

        # Native MOGLabs commands/aliases.
        if p in {"offset", "laser1:offset", "moglabs:offset-percent"}:
            return "offset_percent"
        if p in {"span", "laser1:span"}:
            return "span_percent"
        if p in {"status"}:
            return "status"
        if p in {"report"}:
            return "report"

        return "raw"

    @staticmethod
    def _path_to_raw_command(path: str) -> str:
        """
        Convert a path-like string to a MOGLabs command.
        """
        return path.strip().replace(":", ",").upper()

    async def get(self, path: str):
        """
        Get an actuator/device value.
        """
        kind = self._path_kind(path)

        if kind == "piezo_voltage_v":
            offset_percent = await self.get("moglabs:offset-percent")
            return self._offset_percent_to_voltage(offset_percent)

        if kind == "offset_percent":
            return _parse_first_float(await self.ask("OFFSET"))

        if kind == "span_percent":
            return _parse_first_float(await self.ask("SPAN"))

        if kind == "status":
            return await self.ask("STATUS")

        if kind == "report":
            return await self.ask_dict("REPORT")

        return await self.ask(self._path_to_raw_command(path))

    async def set(self, path: str, value) -> None:
        """
        Set an actuator/device value.
        """
        kind = self._path_kind(path)

        if kind == "piezo_voltage_v":
            voltage = float(value)
            if not 0.0 <= voltage <= self.piezo_full_scale_v:
                raise MOGDLCError(
                    f"Requested piezo voltage {voltage:.6g} V is outside "
                    f"0..{self.piezo_full_scale_v:.6g} V"
                )
            offset_percent = self._voltage_to_offset_percent(voltage)
            await self.set("moglabs:offset-percent", offset_percent)
            return

        if kind == "offset_percent":
            offset_percent = float(value)
            if not -100.0 <= offset_percent <= 100.0:
                raise MOGDLCError(
                    f"Requested OFFSET {offset_percent:.6g} % is outside "
                    "-100..100 %. Reduce SPAN or adjust rails."
                )
            await self.cmd(f"OFFSET,{offset_percent:.9g}")
            return

        if kind == "span_percent":
            span_percent = float(value)
            if not 0.0 <= span_percent <= 100.0:
                raise MOGDLCError(
                    f"Requested SPAN {span_percent:.6g} % is outside 0..100 %"
                )
            await self.cmd(f"SPAN,{span_percent:.9g}")
            return

        await self.cmd(f"{self._path_to_raw_command(path)},{value}")


async def wand_style_frequency_lock(
    *,
    controller: MoglabsDLCproCompat,
    measure_frequency_hz: Callable[[], Union[float, Tuple[bool, float]]],
    f_ref_hz: float,
    set_point_hz: float = 0.0,
    gain_v_per_hz_s: float,
    poll_time_s: float,
    capture_range_hz: float,
    v_pzt_min: float,
    v_pzt_max: float,
    actuator_path: str = "laser1:piezo:voltage-set",
    timeout_s: Optional[float] = None,
    max_step_v: float = 0.25,
) -> None:
    """
    Standalone implementation of the WAnD lock algorithm for testing.

    """
    if poll_time_s <= 0:
        raise ValueError("poll_time_s must be positive")
    if capture_range_hz <= 0:
        raise ValueError("capture_range_hz must be positive")
    if v_pzt_min >= v_pzt_max:
        raise ValueError("v_pzt_min must be less than v_pzt_max")

    locked_at = time.time()

    while True:
        if timeout_s is not None and time.time() > locked_at + timeout_s:
            raise LockLost("Lock timed out")

        await asyncio.sleep(poll_time_s)

        result = measure_frequency_hz()
        if inspect.isawaitable(result):
            result = await result

        if isinstance(result, tuple):
            ok, freq_hz = result
            if not ok:
                continue
            freq_hz = float(freq_hz)
        else:
            freq_hz = float(result)

        delta_hz = freq_hz - f_ref_hz
        f_error_hz = delta_hz - set_point_hz

        if abs(f_error_hz) > capture_range_hz:
            raise LockLost(
                f"Outside capture range: error={f_error_hz:.6g} Hz, "
                f"capture_range={capture_range_hz:.6g} Hz"
            )

        v_error = f_error_hz * gain_v_per_hz_s * poll_time_s
        v_error = min(max(v_error, -max_step_v), max_step_v)

        current_v = float(await controller.get(actuator_path))
        target_v = current_v - v_error

        if target_v < v_pzt_min or target_v > v_pzt_max:
            raise LockLost(
                f"Piezo railed: target={target_v:.6g} V outside "
                f"{v_pzt_min:.6g}..{v_pzt_max:.6g} V"
            )

        await controller.set(actuator_path, target_v)
        logger.debug(
            "freq=%.6f Hz delta=%.6f Hz error=%.6f Hz step=%.6f V piezo=%.6f V",
            freq_hz,
            delta_hz,
            f_error_hz,
            -v_error,
            target_v,
        )
