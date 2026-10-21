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

