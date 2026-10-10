"""Playback position and duration must not be overwritten by scan progress."""
class PlaybackClock:
    def __init__(self):self.reset()
    def reset(self):self.duration=0.;self.position=0.;self.ended=False
    def observe_duration(self,seconds):
        if seconds>0:self.duration=max(self.duration,float(seconds))
    def observe_position(self,seconds,ended=False):
        self.position=max(0.,float(seconds));self.ended=ended
        if self.position>self.duration:self.duration=self.position
    def fraction(self):
        if self.ended:return 1.
        if self.duration<=0:return 0.
        # An underestimated container header is not proof of end of playback.
        return min(.9999,self.position/self.duration)
