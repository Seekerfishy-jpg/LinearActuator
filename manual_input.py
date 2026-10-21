"""
manual_input.py - reads the two USB controllers and turns button presses into
calls on relay_driver. It never sends relay commands itself.

  button 1 = MANUAL   button 2 = AUTO   button 3 = RESET   button 4 = E-STOP
  buttons 5..16 = jog: button N drives relay channel N (odd = RETRACT, even = EXTEND)
"""
import asyncio
import logging
import os
import sys

# Lets pygame receive controller buttons even when its window isn't focused.
# Must be set before pygame is initialised.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
import pygame

import relay_driver as driver

joysticks = []  # keep references or pygame stops delivering events

# controller 0 -> logical buttons 1-8, controller 1 -> logical buttons 9-16
button_mapping = {c: {b: c * 8 + b + 1 for b in range(8)} for c in range(2)}


def init_joysticks(min_count=2):
    pygame.init()
    pygame.display.set_caption("ACTUATOR WINDOW")
    pygame.display.set_mode((300, 100))

    pygame.joystick.init()
    count = pygame.joystick.get_count()
    if count < min_count:
        logging.error("Need %d USB controller(s), found %d!", min_count, count)
        sys.exit(1)

    instance_to_index = {}
    for i in range(count):
        js = pygame.joystick.Joystick(i)
        joysticks.append(js)
        instance_to_index[js.get_instance_id()] = i
        logging.info("Controller %d: %s (instance %d)", i, js.get_name(), js.get_instance_id())
    return instance_to_index


def logical_button(instance_to_index, event):
    idx = instance_to_index.get(event.instance_id)
    return button_mapping.get(idx, {}).get(event.button)


async def poll_joysticks(instance_to_index):
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                await driver.all_off()
                raise SystemExit(0)
            if event.type not in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                continue

            btn = logical_button(instance_to_index, event)
            if btn is None:
                continue
            pressed = event.type == pygame.JOYBUTTONDOWN

            # Control buttons: 1 = MANUAL, 2 = AUTO, 3 = RESET, 4 = E-STOP
            if btn <= 4:
                if pressed:
                    if btn == 4:
                        driver.trigger_e_stop()
                    elif btn == 3:
                        await driver.reset()
                    else:
                        # the driver refuses mode changes while a sequence is moving
                        await driver.set_mode("MANUAL" if btn == 1 else "AUTO")
                continue

            # Jog buttons: 5..16 -> actuators 1..6, odd = RETRACT, even = EXTEND
            if btn >= 5:
                actuator = str((btn - 5) // 2 + 1)
                direction = "RETRACT" if btn % 2 == 1 else "EXTEND"
                await driver.jog(actuator, direction, pressed)

        await asyncio.sleep(0.01)
