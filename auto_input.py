import asyncio
import logging

import relay_driver as driver
from config import INPUT_HOST, INPUT_PORT, NUM_ACTUATORS

TRAVEL_SPEED = 10

def get_inputs():
  auto_inputs = []
  for i in range (1.NUM_ACTUATORS + 1):
    auto_inputs.append(input("Acutator {i}: "))
    return auto_inputs


async def handle_input_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    logging.info("[INPUT] client connected from %s", writer.get_extra_info("peername"))


    try:
        while True:
          for distances in 
    except Exception as e:
        logging.warning("[INPUT] client error: %s", e)
    finally:
        logging.info("[INPUT] client disconnected")
        writer.close()


async def start_server():
    server = await asyncio.start_server(handle_input_client, INPUT_HOST, INPUT_PORT)
    logging.info("[INPUT] Listening on %s:%d for auto-mode distances.", INPUT_HOST, INPUT_PORT)
    return server
