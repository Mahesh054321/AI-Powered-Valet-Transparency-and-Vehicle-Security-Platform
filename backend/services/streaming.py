"""In-memory event streaming bus for real-time GPS telemetry and security alerts.

Supports Server-Sent Events (SSE) subscriptions for authorized clients.
When mobile or tracker GPS fixes are ingested, updates are immediately pushed
to all active subscriber queues without database polling lag.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class StreamBroadcaster:
    """Thread-safe event broadcaster for custody sessions and vehicles."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # topic -> set of subscriber Queue objects
        self._subscribers: dict[str, set[queue.Queue]] = {}

    def subscribe(self, topic: str) -> queue.Queue:
        """Register a new subscriber queue for a given topic."""
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            if topic not in self._subscribers:
                self._subscribers[topic] = set()
            self._subscribers[topic].add(q)
            count = len(self._subscribers[topic])
        logger.debug("Subscriber connected to topic %s (total: %d)", topic, count)
        return q

    def unsubscribe(self, topic: str, q: queue.Queue) -> None:
        """Remove a subscriber queue when client disconnects."""
        with self._lock:
            if topic in self._subscribers:
                self._subscribers[topic].discard(q)
                if not self._subscribers[topic]:
                    del self._subscribers[topic]
        logger.debug("Subscriber disconnected from topic %s", topic)

    def publish(self, topic: str, event_type: str, data: dict[str, Any]) -> int:
        """Push an event to all subscribers listening to topic."""
        with self._lock:
            subscribers = list(self._subscribers.get(topic, set()))

        if not subscribers:
            return 0

        payload = {
            "event": event_type,
            "data": data,
            "timestamp": time.time(),
        }

        delivered = 0
        for q in subscribers:
            try:
                q.put_nowait(payload)
                delivered += 1
            except queue.Full:
                logger.warning("Subscriber queue full for topic %s, dropping event", topic)

        return delivered


# Global broadcaster instance for the application process
broadcaster = StreamBroadcaster()


def format_sse(event: str, data: dict[str, Any], event_id: str | None = None) -> str:
    """Format an SSE frame according to the W3C EventSource spec."""
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    # Single-line or JSON dump
    json_str = json.dumps(data)
    lines.append(f"data: {json_str}")
    lines.append("\n")
    return "\n".join(lines)


sse_event = format_sse
