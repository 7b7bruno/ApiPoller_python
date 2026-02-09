import json
import os
import logging
import subprocess
import time
from typing import Dict, Any, Optional, Callable
from datetime import datetime


class RecoveryManager:
    """
    Manages recovery escalation for critical network operations.

    When critical operations (like acknowledgments) fail after all retries:
    1. First escalation: Reboot the modem
    2. Second escalation: Reboot the Pi

    Also maintains a persistent queue of pending acknowledgments
    that survives system reboots.
    """

    def __init__(self,
                 queue_file: str = "pending_acks.json",
                 modem_reboot_callback: Optional[Callable] = None):
        """
        Initialize RecoveryManager.

        Args:
            queue_file: Path to persistent queue file
            modem_reboot_callback: Function to call for modem reboot
        """
        self.queue_file = queue_file
        self.modem_reboot_callback = modem_reboot_callback
        self.pending_acks = self._load_queue()
        self.modem_rebooted = False

    def _load_queue(self) -> Dict[str, Any]:
        """Load pending acknowledgments from persistent storage"""
        if os.path.exists(self.queue_file):
            try:
                with open(self.queue_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Failed to load pending acks queue: {e}")
                return {}
        return {}

    def _save_queue(self):
        """Save pending acknowledgments to persistent storage"""
        try:
            with open(self.queue_file, 'w') as f:
                json.dump(self.pending_acks, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save pending acks queue: {e}")

    def add_pending_ack(self, ack_id: str, ack_data: Dict[str, Any]):
        """
        Add an acknowledgment to the pending queue.

        Args:
            ack_id: Unique identifier for the ack (message_id or command_id)
            ack_data: Data needed to retry the ack (url, headers, etc.)
        """
        self.pending_acks[ack_id] = {
            'data': ack_data,
            'timestamp': datetime.now().isoformat(),
            'retry_count': 0
        }
        self._save_queue()
        logging.info(f"Added pending ack to queue: {ack_id}")

    def remove_pending_ack(self, ack_id: str):
        """Remove an acknowledgment from the pending queue after success"""
        if ack_id in self.pending_acks:
            del self.pending_acks[ack_id]
            self._save_queue()
            logging.info(f"Removed pending ack from queue: {ack_id}")

    def get_pending_acks(self) -> Dict[str, Any]:
        """Get all pending acknowledgments"""
        return self.pending_acks

    def escalate_modem_reboot(self) -> bool:
        """
        First escalation: Reboot the modem.

        Returns:
            True if reboot was triggered, False otherwise
        """
        if self.modem_rebooted:
            logging.warning("Modem already rebooted in this recovery cycle")
            return False

        logging.warning("ESCALATION: Triggering modem reboot due to persistent network failures")

        if self.modem_reboot_callback:
            try:
                self.modem_reboot_callback()
                self.modem_rebooted = True
                logging.info("Modem reboot initiated successfully")
                return True
            except Exception as e:
                logging.error(f"Failed to reboot modem: {e}")
                return False
        else:
            logging.error("No modem reboot callback configured")
            return False

    def escalate_pi_reboot(self):
        """
        Final escalation: Reboot the Raspberry Pi.

        This is a last resort when modem reboot doesn't resolve the issue.
        Pending acks are saved to persistent storage before reboot.
        """
        logging.critical("FINAL ESCALATION: Rebooting Raspberry Pi due to unrecoverable network failures")

        # Ensure pending acks are saved
        self._save_queue()

        try:
            # Reboot the Pi
            subprocess.run(['sudo', 'reboot'], check=True)
        except Exception as e:
            logging.error(f"Failed to reboot Pi: {e}")
            raise

    def reset_escalation_state(self):
        """Reset escalation state after successful recovery"""
        self.modem_rebooted = False
        logging.info("Recovery escalation state reset")

    def handle_critical_failure(self,
                               operation_name: str,
                               ack_id: str,
                               ack_data: Dict[str, Any],
                               retry_callback: Callable,
                               max_reboots: int = 5,
                               initial_wait: int = 60,
                               backoff_factor: float = 2.0,
                               max_wait: int = 1800) -> bool:
        """
        Handle failure of a critical operation by rebooting the modem
        with exponential backoff until the operation succeeds.

        Args:
            operation_name: Name of the operation (for logging)
            ack_id: Unique identifier for the acknowledgment
            ack_data: Data needed to retry
            retry_callback: Function to call to retry the operation
            max_reboots: Maximum number of modem reboot attempts
            initial_wait: Seconds to wait after first reboot
            backoff_factor: Multiplier for wait time between reboots
            max_wait: Maximum wait time in seconds

        Returns:
            True if operation succeeded after recovery, False otherwise
        """
        logging.error(f"Critical operation failed: {operation_name} (ID: {ack_id})")

        # Add to persistent queue
        self.add_pending_ack(ack_id, ack_data)

        wait_time = initial_wait

        for attempt in range(max_reboots):
            logging.warning(f"Modem reboot attempt {attempt + 1}/{max_reboots} for {operation_name}")

            # Reset so escalate_modem_reboot() allows another reboot
            self.modem_rebooted = False

            if self.escalate_modem_reboot():
                logging.info(f"Waiting {wait_time}s for modem to restart...")
                time.sleep(wait_time)

                # Retry the operation
                try:
                    logging.info(f"Retrying {operation_name} after modem reboot (attempt {attempt + 1})...")
                    result = retry_callback()
                    if result:
                        logging.info(f"{operation_name} succeeded after modem reboot")
                        self.remove_pending_ack(ack_id)
                        self.reset_escalation_state()
                        return True
                except Exception as e:
                    logging.error(f"{operation_name} still failing after modem reboot: {e}")

                # Exponential backoff for next attempt
                wait_time = min(wait_time * backoff_factor, max_wait)
            else:
                logging.error(f"Failed to trigger modem reboot on attempt {attempt + 1}")

        logging.error(f"{operation_name} failed after {max_reboots} modem reboot attempts. Giving up.")
        return False

    def retry_pending_acks(self, retry_callback: Callable[[str, Dict], bool]):
        """
        Retry all pending acknowledgments from the queue.

        Should be called on application startup to handle acks that
        were pending when the system rebooted.

        Args:
            retry_callback: Function(ack_id, ack_data) that returns True on success
        """
        if not self.pending_acks:
            return

        logging.info(f"Found {len(self.pending_acks)} pending acknowledgments to retry")

        acks_to_retry = list(self.pending_acks.items())
        for ack_id, ack_entry in acks_to_retry:
            ack_data = ack_entry['data']
            retry_count = ack_entry.get('retry_count', 0)

            logging.info(f"Retrying pending ack: {ack_id} (attempt {retry_count + 1})")

            try:
                if retry_callback(ack_id, ack_data):
                    logging.info(f"Successfully sent pending ack: {ack_id}")
                    self.remove_pending_ack(ack_id)
                else:
                    # Increment retry count
                    self.pending_acks[ack_id]['retry_count'] = retry_count + 1
                    self._save_queue()
                    logging.warning(f"Failed to retry pending ack: {ack_id}")
            except Exception as e:
                # Increment retry count
                self.pending_acks[ack_id]['retry_count'] = retry_count + 1
                self._save_queue()
                logging.error(f"Error retrying pending ack {ack_id}: {e}")
