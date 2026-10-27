"""Speech endpoint decisions independent of audio devices or ML packages."""
from collections import deque
from dataclasses import dataclass


class UtteranceTooLong(Exception):
    pass


@dataclass
class SpeechSegmenter:
    silence_frames: int = 14  # 448 ms with 512 samples at 16 kHz.
    pre_roll_frames: int = 10
    min_speech_frames: int = 3
    max_frames: int = 625  # 20 seconds; never transcribe a truncated command.

    def __post_init__(self):
        if min(self.silence_frames, self.pre_roll_frames, self.min_speech_frames, self.max_frames) < 1:
            raise ValueError('Frame limits must be positive')
        self.reset()

    def reset(self):
        self.pre_roll = deque(maxlen=self.pre_roll_frames)
        self.frames = []
        self.started = False
        self.speech_count = self.silent_count = 0

    def feed(self, frame, speech: bool):
        if not self.started:
            self.pre_roll.append(frame)
            if not speech:
                return None
            self.started = True
            self.frames = list(self.pre_roll)
        else:
            self.frames.append(frame)
        if speech:
            self.speech_count += 1
            self.silent_count = 0
        else:
            self.silent_count += 1
        if len(self.frames) >= self.max_frames:
            raise UtteranceTooLong('Please use a shorter sentence; no partial command was submitted.')
        if self.silent_count >= self.silence_frames:
            frames = self.frames if self.speech_count >= self.min_speech_frames else None
            self.reset()
            return frames
        return None
