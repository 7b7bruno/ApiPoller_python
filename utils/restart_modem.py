import sys
import subprocess
import time

DEFAULT_WAIT_TIME = 60
DEFAULT_RESTART_DELAY = 5
ON_COMMAND = ['sudo', 'uhubctl', '-l', '1-1', '-p', '3', '-a', 'on']
OFF_COMMAND = ['sudo', 'uhubctl', '-l', '1-1', '-p', '3', '-a', 'off']

def restartModem(waitTime, restartDelay): 
    print("Restarting modem...")
    print("Powering off USB port...")
    subprocess.run(OFF_COMMAND)
    print(f"Waiting for {restartDelay} seconds before powering back on...")
    time.sleep(restartDelay)
    print("Powering on USB port...")
    subprocess.run(ON_COMMAND)
    print(f"Waiting for modem to boot for {waitTime} seconds...")
    time.sleep(waitTime)
    print("Modem restarted.")


if __name__ == '__main__':
    if len(sys.argv) == 1:
        waitTime = DEFAULT_WAIT_TIME
        restartDelay = DEFAULT_RESTART_DELAY
    elif len(sys.argv) == 2:
        waitTime = int(sys.argv[1])
        restartDelay = DEFAULT_RESTART_DELAY
    elif len(sys.argv) == 3:
        waitTime = int(sys.argv[1])
        restartDelay = int(sys.argv[2])
    else:
        waitTime = DEFAULT_WAIT_TIME
        restartDelay = DEFAULT_RESTART_DELAY
        print(f"Incorrect argument syntax! Should pe python restart_modem.py [wait_time] [restart_delay]")

    if waitTime == DEFAULT_WAIT_TIME:
        print(f"Using default wait time: {waitTime}")
    else:
        print(f"Using custom wait time: {waitTime}")
    if restartDelay == DEFAULT_RESTART_DELAY:
        print(f"Using default restart delay: {restartDelay}")
    else:
        print(f"Using custom restart delay: {restartDelay}")

    restartModem(waitTime, restartDelay)
    