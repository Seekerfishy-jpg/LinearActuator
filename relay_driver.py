"""
relay_driver.py - the ONLY file that talks to the relay board.

All the safety rules live here. The input files (manual_input.py and
auto_input.py) just call the functions below; they never send relay commands.

  jog(actuator, direction, pressed)    manual button press / release
  trigger_e_stop() / emergency_stop()  stop everything
  set_mode("MANUAL" | "AUTO")          switch mode (refused while a sequence is moving)
  reset()                              clear an e-stop (button 3)
  submit_job(moves, on_result)         auto mode: queue one set of moves
  auto_runner()                        background task that runs queued jobs
  status_text()                        one-line status for the other program

Other files may READ driver.current_mode, driver.e_stop_triggered and
driver.auto_busy, but should only change them through the functions above.
"""
import asyncio
import logging
import math

from config import (FIRST_COIL, LISTEN_HOST, LISTEN_PORT, MAX_STROKE_MM,
                    NUM_ACTUATORS, TRAVEL_SPEED)

# ---------------------------------------------------------------- state
current_mode = "MANUAL"
e_stop_triggered = False
auto_busy = False           # True while a sequence is moving actuators

active_writer = None        # the board's connection, or None when not connected
_send_lock = None           # created in init()
_pending_job = None         # accepted from the auto input, not started yet
_background_tasks = set()


def init():
    """Call once, from inside the running event loop, before anything else."""
    global _send_lock
    _send_lock = asyncio.Lock()


def spawn(coro):
    """create_task, but keep a reference so the task can't be garbage collected."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


# ---------------------------------------------------------------- Modbus RTU
def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc.to_bytes(2, "little")


def coil_cmd(coil: int, on: bool) -> bytes:
    frame = bytes([0x01, 0x05, 0x00, coil, 0xFF if on else 0x00, 0x00])
    return frame + crc16(frame)


def coil_for(actuator: int, direction: str) -> int:
    """Actuator 1 = relay channels 5/6, actuator 2 = 7/8 ... actuator 6 = 15/16.
    Odd channel = RETRACT, even channel = EXTEND. Channel N is coil address N-1."""
    return FIRST_COIL + 2 * (actuator - 1) + (1 if direction == "EXTEND" else 0)


assert coil_for(NUM_ACTUATORS, "EXTEND") < 16, "board only has 16 coils"

relays = {
    str(n): {
        d: {"ON": coil_cmd(coil_for(n, d), True), "OFF": coil_cmd(coil_for(n, d), False)}
        for d in ("RETRACT", "EXTEND")
    }
    for n in range(1, NUM_ACTUATORS + 1)
}

_all_off_frame = bytes.fromhex("01 0F 00 00 00 10 02 00 00")  # write 16 coils = 0
TURN_ALL_OFF = _all_off_frame + crc16(_all_off_frame)


# ---------------------------------------------------------------- board connection
def board_ok() -> bool:
    return active_writer is not None and not active_writer.is_closing()


async def send_cmd(cmd: bytes) -> bool:
    if not board_ok():
        logging.warning("[NETWORK] Relay board is not connected!")
        return False
    try:
        async with _send_lock:
            active_writer.write(cmd)
            await active_writer.drain()
        return True
    except Exception as e:
        logging.error("[NETWORK ERROR] %s", e)
        return False


async def all_off() -> bool:
    return await send_cmd(TURN_ALL_OFF)


async def handle_board(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Only this function reads from the socket (a StreamReader allows one reader)."""
    global active_writer
    active_writer = writer
    logging.info("*** BOARD CONNECTED from %s ***", writer.get_extra_info("peername"))
    try:
        while True:
            data = await reader.read(100)
            if not data:
                break
            logging.debug("Board reply: %s", data.hex(" "))
    except Exception:
        pass
    finally:
        logging.warning("*** BOARD DISCONNECTED ***")
        if active_writer is writer:
            active_writer = None


async def start_board_server():
    server = await asyncio.start_server(handle_board, LISTEN_HOST, LISTEN_PORT)
    logging.info("[SERVER] Listening on port %d... waiting for relay board.", LISTEN_PORT)
    return server


# ---------------------------------------------------------------- safety actions
async def emergency_stop():
    global e_stop_triggered
    e_stop_triggered = True
    _cancel_pending("e-stop")
    logging.critical("!!! EMERGENCY STOP !!! TURNING OFF ALL RELAYS")
    await all_off()


def trigger_e_stop():
    """For callers that can't await: the flag is set immediately, the relays
    are switched off a moment later."""
    global e_stop_triggered
    e_stop_triggered = True
    spawn(emergency_stop())


async def set_mode(new_mode: str) -> bool:
    """Switch to MANUAL or AUTO. An e-stop stays latched until reset().
    Refused while an auto sequence is moving, so a stray button can't interrupt it."""
    global current_mode
    if auto_busy:
        logging.info("Mode change ignored: auto sequence is moving")
        return False
    _cancel_pending("mode changed")
    if new_mode != current_mode:
        logging.info("Switching to %s mode", new_mode)
    current_mode = new_mode
    if new_mode == "AUTO":
        logging.info("AUTO mode: waiting for a job from the auto input")
    await all_off()  # nothing stays on across a mode change
    return True


async def reset() -> bool:
    """Clear an e-stop (button 3). The mode stays as it is.
    Refused for the instant an auto sequence is still stopping."""
    global e_stop_triggered
    if auto_busy:
        logging.info("Reset ignored: auto sequence is still stopping")
        return False
    e_stop_triggered = False
    logging.info("E-stop reset (%s mode)", current_mode)
    await all_off()
    return True


# ---------------------------------------------------------------- manual mode
async def jog(actuator: str, direction: str, pressed: bool):
    """A manual jog button. A press only works in MANUAL with no e-stop.
    A release always sends OFF, so a relay can never get stuck on."""
    channel = coil_for(int(actuator), direction) + 1
    if pressed:
        if current_mode != "MANUAL" or e_stop_triggered:
            return
        logging.info("[MANUAL] actuator %s (relay channel %d) | %s | ON", actuator, channel, direction)
        await send_cmd(relays[actuator][direction]["ON"])
    else:
        logging.info("[MANUAL] actuator %s (relay channel %d) | %s | OFF", actuator, channel, direction)
        await send_cmd(relays[actuator][direction]["OFF"])


# ---------------------------------------------------------------- auto mode
def reject_reason():
    """Why a new job can't be accepted right now, or None if it can."""
    if not board_ok():
        return "relay board not connected"
    if e_stop_triggered:
        return "e-stop active (press button 3 on the controller to reset)"
    if current_mode != "AUTO":
        return "not in AUTO mode (press button 2 on the controller)"
    if auto_busy or _pending_job is not None:
        return "busy"
    return None


def _check_moves(moves):
    for actuator, dist in moves:
        if not math.isfinite(dist) or abs(dist) > MAX_STROKE_MM:
            return f"actuator {actuator}: {dist:g} is outside +/-{MAX_STROKE_MM:g} mm"
    return None


def submit_job(moves, on_result):
    """Queue one set of moves: a list of (actuator "1".."6", distance_mm).
    Returns None if accepted, otherwise the reason it was rejected.
    on_result(text) is called later with "DONE" or "STOPPED <reason>"."""
    global _pending_job
    reason = reject_reason() or _check_moves(moves)  # no await in here, so two callers can't both get in
    if reason:
        return reason
    _pending_job = {"moves": moves, "on_result": on_result}
    logging.info("[AUTO] Job accepted: %s", ", ".join(f"{d:g}" for _, d in moves))
    return None


def _notify(job, text: str):
    try:
        job["on_result"](text)
    except Exception:
        pass


def _cancel_pending(reason: str):
    """Throw away a job that was accepted but hasn't started moving yet."""
    global _pending_job
    if _pending_job is not None:
        logging.info("[AUTO] Pending job cancelled: %s", reason)
        _notify(_pending_job, f"STOPPED {reason}")
        _pending_job = None


async def _run_moves(job):
    """Run the moves one after another, then report DONE or STOPPED <reason>."""
    global auto_busy
    auto_busy = True
    result = "STOPPED unknown error"
    loop = asyncio.get_running_loop()
    logging.info("[AUTO] Sequence started")
    try:
        for actuator, distance in job["moves"]:
            if e_stop_triggered:
                result = "STOPPED e-stop"
                return
            if distance == 0:
                logging.info("[AUTO] Actuator %s: 0 mm -> skipping", actuator)
                continue

            direction = "EXTEND" if distance > 0 else "RETRACT"
            duration = abs(distance) / TRAVEL_SPEED
            logging.info("[AUTO] Actuator %s: %s %g mm (%.2fs)", actuator, direction, abs(distance), duration)

            try:
                start = loop.time()  # start the clock when ON is sent
                if not await send_cmd(relays[actuator][direction]["ON"]):
                    result = "STOPPED relay board not connected"
                    return
                while loop.time() - start < duration:
                    if e_stop_triggered:
                        logging.info("[AUTO] Actuator %s stopped early", actuator)
                        result = "STOPPED e-stop"
                        return
                    await asyncio.sleep(0.005)
            finally:
                # Always runs: normal finish, early stop, or crash
                await send_cmd(relays[actuator][direction]["OFF"])
            await asyncio.sleep(0.1)

        result = "DONE"
        logging.info("[AUTO] Sequence complete")
    except asyncio.CancelledError:
        result = "STOPPED program shutting down"
        raise
    except Exception as e:
        logging.exception("[AUTO] Unexpected error")
        result = f"STOPPED error: {e}"
    finally:
        auto_busy = False
        _notify(job, result)


async def auto_runner():
    """Background task: runs a queued job as soon as AUTO mode is active."""
    global _pending_job
    while True:
        if current_mode == "AUTO" and not e_stop_triggered and _pending_job is not None:
            job, _pending_job = _pending_job, None
            await _run_moves(job)
        await asyncio.sleep(0.01)


def status_text() -> str:
    busy = int(auto_busy or _pending_job is not None)
    return f"mode={current_mode} busy={busy} estop={int(e_stop_triggered)} board={int(board_ok())}"
