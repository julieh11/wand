import asyncio
import logging

from wand.drivers.moglabs_ddlc import (
    MoglabsDLCproCompat,
    wand_style_frequency_lock,
    LockLost,
)

logging.basicConfig(level=logging.INFO)

TARGET_FREQUENCY_HZ = 4.517872387239e14  # 663.57 nm in vacuum


async def read_wavemeter_frequency_hz() -> float:
  
    # wavemeter readout
    #return highfinesse.get_frequency(channel=1)
    #return wand_wavemeter_client.frequency_hz("moglabs_663")
    #return my_meter.read_frequency_hz()
    
    raise NotImplementedError("Connect this to wavemeter frequency readout")


async def main():
    laser = MoglabsDLCproCompat(
        addr="10.1.1.90",          # dDLC IP, e.g.
        port=None,                 # Defaults to MOGLabs TCP port 7802
        timeout=1.0,
        piezo_full_scale_v=150.0,
        set_span_zero_on_open=True,
    )

    await laser.open()

    try:
        status = await laser.get("status")
        logging.info("MOGLabs dDLC status: %s", status)

        # Ensure the dDLC is not sweeping while the software lock runs.
        await laser.set("SPAN", 0.0)

        # can move near the middle of the piezo range before locking.
        # This maps to OFFSET ~= 0%.
        await laser.set("laser1:piezo:voltage-set", 75.0)

        await wand_style_frequency_lock(
            controller=laser,
            measure_frequency_hz=read_wavemeter_frequency_hz,
            f_ref_hz=TARGET_FREQUENCY_HZ,
            set_point_hz=0.0,
          
            gain_v_per_hz_s=1e-10,

            poll_time_s=1.0,
            capture_range_hz=2e9,
          
            v_pzt_min=45.0,
            v_pzt_max=105.0,

            actuator_path="laser1:piezo:voltage-set",
            max_step_v=0.25,
        )

    except LockLost as exc:
        logging.error("Lock dropped: %s", exc)

    finally:
        await laser.close()


if __name__ == "__main__":
    asyncio.run(main())
