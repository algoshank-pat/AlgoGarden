"""Local microphone -> Silero -> Parakeet -> shared API -> Piper -> speaker."""
import argparse
from pathlib import Path

HERE = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', default='http://127.0.0.1:8010')
    parser.add_argument('--list-devices', action='store_true')
    parser.add_argument('--input-device', type=int)
    parser.add_argument('--output-device', type=int)
    parser.add_argument('--stt-backend', choices=['parakeet','whisper'], default='parakeet')
    parser.add_argument('--stt-model', default='small', help='Whisper model when --stt-backend whisper is selected.')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--phone-region',default='IN',help='Phone parsing region; match the restaurant configuration.')
    parser.add_argument('--piper-model', type=Path, default=HERE/'models/piper/en_US-lessac-medium.onnx')
    args = parser.parse_args()
    stt = None
    try:
        import sounddevice as sd
        if args.list_devices:
            print(sd.query_devices())
            return
        from voice.adapters import SileroMicrophone, WhisperTranscriber, ParakeetTranscriber, PiperSpeaker
        from voice.runner import VoiceRunner
        from voice.transport import VoiceService
        # Load/check models before opening a conversation or capturing audio.
        stt = (ParakeetTranscriber(HERE,args.threads,args.phone_region) if args.stt_backend=='parakeet'
               else WhisperTranscriber(args.stt_model, HERE/'models/whisper', args.threads,args.phone_region))
        tts = PiperSpeaker(args.piper_model, args.output_device)
        import yaml
        restaurant=yaml.safe_load((HERE/'restaurants/demo_restaurant.yaml').read_text(encoding='utf-8'))
        tts.prime([f"Thank you for calling {restaurant['name']}. How can I help you today?",
                   'Do you have a reservation with us?','What phone number can I use for your reservation?',
                   'May I have your name for the reservation?'])
        listener = SileroMicrophone(args.input_device)
        sd.check_input_settings(device=args.input_device, samplerate=16000, channels=1, dtype='float32')
        sd.check_output_settings(device=args.output_device, samplerate=tts.voice.config.sample_rate, channels=1, dtype='int16')
        service=VoiceService(args.api_url)
        service.warmup()
        VoiceRunner(listener, stt, tts, service, HERE/'voice_metrics.jsonl').run()
    except KeyboardInterrupt:
        print('\nVoice session ended.')
    except ImportError as error:
        parser.exit(1, f'Voice dependency unavailable: {error}. Run uv sync --extra voice.\n')
    except Exception as error:
        parser.exit(1, f'Voice stopped: {error}\nCheck the API, Ollama, model files and audio devices; an interrupted booking may need staff verification.\n')

    finally:
        if stt is not None and hasattr(stt,'close'): stt.close()


if __name__ == '__main__':
    main()
