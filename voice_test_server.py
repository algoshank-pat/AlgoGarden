"""Private harness API; closing its stdin gracefully stops models and Uvicorn."""
import sys
import os
import faulthandler
from threading import Thread
import uvicorn


if __name__=='__main__':
    # Load native numerical libraries on the main thread before AnyIO creates
    # request workers (Windows DLL/thread initialization can otherwise deadlock).
    import numpy
    from piper import PiperVoice
    faulthandler.enable()
    server=uvicorn.Server(uvicorn.Config('chat_api:app',host='127.0.0.1',port=int(sys.argv[1]),access_log=False))
    def wait_for_parent():
        os.read(sys.stdin.fileno(),1)
        server.should_exit=True
    Thread(target=wait_for_parent,daemon=True).start()
    server.run()
