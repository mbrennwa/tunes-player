"""Tests for USB IRQ CPU detection."""

from __future__ import annotations

import unittest
import unittest.mock

from tunes_player.platform.linux import usb_irq


class UsbIrqTests(unittest.TestCase):
    def test_xhci_irq_cpu_picks_busiest_cpu(self) -> None:
        sample = """
           CPU0       CPU1       CPU2
 141:          0          0    8392012          IR-PCI-MSI 52428800-xhci_hcd
 142:          0          5          0          IR-PCI-MSI 52428801-xhci_hcd
"""
        with unittest.mock.patch.object(
            usb_irq.Path,
            "read_text",
            side_effect=[sample, "processor\t:\nprocessor\t:\nprocessor\t:\n"],
        ):
            self.assertEqual(usb_irq.xhci_irq_cpu(), 2)

    def test_xhci_irq_numbers(self) -> None:
        sample = """
 141: 1 0 0 xhci_hcd
 999: 0 0 0 i915
"""
        with unittest.mock.patch.object(usb_irq.Path, "read_text", return_value=sample):
            self.assertEqual(usb_irq.xhci_irq_numbers(), [141])


if __name__ == "__main__":
    unittest.main()
