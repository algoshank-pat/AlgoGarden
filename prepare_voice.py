"""Download public CPU speech models once; no microphone/speaker access."""
from pathlib import Path


def main():
    from faster_whisper.utils import download_model
    from piper.download_voices import download_voice
    root = Path(__file__).parent/'models'
    (root/'piper').mkdir(parents=True, exist_ok=True)
    print('Downloading Piper English voice…', flush=True)
    download_voice('en_US-lessac-high', root/'piper')
    print('Downloading faster-whisper small…', flush=True)
    download_model('small', cache_dir=str(root/'whisper'))
    print('Voice models ready.', flush=True)


if __name__ == '__main__':
    main()
