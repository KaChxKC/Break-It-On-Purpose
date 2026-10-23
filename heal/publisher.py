"""The detection end of the self-healing loop: score live features and, on a
sustained anomaly, publish an alert to SNS (which the responder Lambda acts on).

A sustained-run requirement plus a cooldown debounces the signal, so transient
flicker doesn't fire the loop and a burst of anomalies doesn't publish repeatedly
while the system is already recovering.
"""

import json
import time

from infra.aws import Aws
from model.model import Detector


class DetectorPublisher:
    def __init__(self, aws: Aws, topic_arn: str, detector: Detector,
                 k: int = 3, cooldown_s: float = 120.0) -> None:
        self.aws = aws
        self.topic_arn = topic_arn
        self.detector = detector
        self.k = k
        self.cooldown_s = cooldown_s
        self._run = 0
        self._last_publish = 0.0

    def process(self, feats) -> bool:
        """Score the latest sample; publish if anomalies are sustained and the
        cooldown has elapsed. Returns whether an alert was published."""
        if len(feats) == 0:
            return False
        latest_is_anomaly = bool(self.detector.is_anomaly(feats)[-1])
        self._run = self._run + 1 if latest_is_anomaly else 0

        now = time.time()
        if self._run >= self.k and (now - self._last_publish) >= self.cooldown_s:
            score = float(self.detector.anomaly_score(feats)[-1])
            self.aws.client("sns").publish(
                TopicArn=self.topic_arn,
                Subject="biop-anomaly",
                Message=json.dumps({"anomaly_score": score, "ts": now}),
            )
            self._last_publish = now
            self._run = 0  # reset so we don't re-fire while recovery is underway
            return True
        return False
