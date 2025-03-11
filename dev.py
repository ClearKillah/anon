import sys
import time
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import subprocess
import os

logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(message)s',
                   datefmt='%Y-%m-%d %H:%M:%S')

class BotReloader(FileSystemEventHandler):
    def __init__(self):
        self.process = None
        self.start_bot()

    def start_bot(self):
        if self.process:
            self.process.terminate()
            self.process.wait()
        logging.info('Starting bot...')
        self.process = subprocess.Popen([sys.executable, 'bot.py'])

    def on_modified(self, event):
        if event.src_path.endswith('.py'):
            logging.info(f'Detected change in {event.src_path}')
            self.start_bot()

if __name__ == "__main__":
    path = '.'
    event_handler = BotReloader()
    observer = Observer()
    observer.schedule(event_handler, path, recursive=False)
    observer.start()
    logging.info('Watching for file changes...')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        if event_handler.process:
            event_handler.process.terminate()
        logging.info('Stopping...')
    observer.join() 