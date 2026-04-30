# test to determine sign of gain
# if increasing OFFSET increases the measured optical frequency, use a positive gain. 
# If increasing OFFSET decreases the measured optical frequency, use a negative gain.

async def sign_test():
    laser = MoglabsDLCproCompat("10.1.1.90", piezo_full_scale_v=150.0)
    await laser.open()

    try:
        await laser.set("SPAN", 0.0)

        v0 = float(await laser.get("laser1:piezo:voltage-set"))
        f0 = await read_wavemeter_frequency_hz()

        await laser.set("laser1:piezo:voltage-set", v0 + 0.10)
        await asyncio.sleep(2.0)
        f1 = await read_wavemeter_frequency_hz()

        df_dv = (f1 - f0) / 0.10
        print(f"df/dV = {df_dv:.3e} Hz/V")

        # Restore original actuator value.
        await laser.set("laser1:piezo:voltage-set", v0)

        if df_dv > 0:
            print("Use positive lock_gain, e.g. +1e-10")
        else:
            print("Use negative lock_gain, e.g. -1e-10")

    finally:
        await laser.close()
