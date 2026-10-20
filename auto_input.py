"""
auto_input.py - listens for the other program's distances and hands them to
relay_driver. It never sends relay commands itself.

The other program connects to INPUT_PORT over TCP and sends plain text, one
message per line:

  send   10, -20, 0, 5, 0, 30    six distances in mm for actuators 1..6
                                 (+ = extend, - = retract, 0 = skip)
  reply  ACK                     accepted, the actuators start moving
    or   ERR <reason>            rejected, nothing moves
  later  DONE                    all six moves finished
    or   STOPPED <reason>        e-stop, board lost, or cancelled
  send   STATUS                  reply: STATUS mode=AUTO busy=0 estop=0 board=1
"""
import asyncio
import logging

import relay_driver as driver
from config import INPUT_HOST, INPUT_PORT, NUM_ACTUATORS


def parse_distances(line: str):
    """'10, -20, 0, 5, 0, 30' -> [('1', 10.0), ('2', -20.0), ...].
    Raises ValueError with a readable reason. (The range check is the driver's job.)"""
    parts = line.replace(",", " ").split()
    if len(parts) != NUM_ACTUATORS:
        raise ValueError(f"expected {NUM_ACTUATORS} numbers, got {len(parts)}")
    moves = []
    for i, text in enumerate(parts, start=1):
        try:
            dist = float(text)
        except ValueError:
            raise ValueError(f"actuator {i}: '{text}' is not a number") from None
        moves.append((str(i), dist))
    return moves


async def handle_input_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    logging.info("[INPUT] client connected from %s", writer.get_extra_info("peername"))

    def reply(text: str):
        if not writer.is_closing():
            try:
                writer.write((text + "\n").encode())
            except Exception:
                pass

    try:
        while True:
            raw = await reader.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").strip()
            if not line:
                continue

            if line.upper() == "STATUS":
                reply("STATUS " + driver.status_text())
                continue

            try:
                moves = parse_distances(line)
            except ValueError as e:
                reply(f"ERR {e}")
                continue

            reason = driver.submit_job(moves, on_result=reply)  # reply() is called again when it finishes
            reply(f"ERR {reason}" if reason else "ACK")
    except Exception as e:
        logging.warning("[INPUT] client error: %s", e)
    finally:
        logging.info("[INPUT] client disconnected")
        writer.close()


async def start_server():
    server = await asyncio.start_server(handle_input_client, INPUT_HOST, INPUT_PORT)
    logging.info("[INPUT] Listening on %s:%d for auto-mode distances.", INPUT_HOST, INPUT_PORT)
    return server
