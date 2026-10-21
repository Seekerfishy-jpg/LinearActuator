"""
Start here:   python main.py

Wires the three parts together:
  relay_driver.py   talks to the board, holds the safety rules
  manual_input.py   USB controllers -> driver
  auto_input.py     other program's distances -> driver
Settings are in config.py.
"""
import asyncio
import logging

import auto_input
import manual_input
import relay_driver as driver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def main():
    driver.init()
    instance_to_index = manual_input.init_joysticks()

    board_server = await driver.start_board_server()
    input_server = await auto_input.start_server()

    tasks = [
        asyncio.create_task(manual_input.poll_joysticks(instance_to_index)),
        asyncio.create_task(driver.auto_runner()),
    ]
    try:
        # gather so that a crash in any task is raised instead of vanishing
        await asyncio.gather(board_server.serve_forever(), input_server.serve_forever(), *tasks)
    finally:
        # Ctrl+C or a crash: leave nothing energised, then stop listening.
        # (Deliberately not "async with server": on Python 3.12+ that waits for the
        # board's connection to close first, so Ctrl+C would hang.)
        await driver.all_off()
        board_server.close()
        input_server.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
