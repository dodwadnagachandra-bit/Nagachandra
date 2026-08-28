"""CAN Bus BMU Simulator -- generates realistic BMU Layer 2 traffic on vcan."""

from tools.simulators.can_sim.signals import SignalGenerator
from tools.simulators.can_sim.simulator import CANSimulator

__all__ = ["CANSimulator", "SignalGenerator"]
