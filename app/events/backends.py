from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Callable, Protocol

from app.config import settings


EventHandler = Callable[[dict[str, Any]], None]


class EventBus(Protocol):
    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        ...

    def publish(self, event_type: str, envelope: dict[str, Any]) -> None:
        ...


class InMemoryEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        self._subscribers[event_type].append(handler)

    def publish(self, event_type: str, envelope: dict[str, Any]) -> None:
        for handler in self._subscribers.get(event_type, []):
            handler(envelope)
        for handler in self._subscribers.get("*", []):
            handler(envelope)


class KafkaEventBus(InMemoryEventBus):
    def __init__(self, bootstrap_servers: str, topic: str) -> None:
        super().__init__()
        self.topic = topic
        self._producer = None
        try:
            from confluent_kafka import Producer  # type: ignore

            self._producer = Producer({"bootstrap.servers": bootstrap_servers})
        except Exception:
            self._producer = None

    def publish(self, event_type: str, envelope: dict[str, Any]) -> None:
        super().publish(event_type, envelope)
        if not self._producer:
            return
        payload = json.dumps({"event_type": event_type, **envelope}, default=str).encode("utf-8")
        try:
            self._producer.produce(self.topic, payload)
            self._producer.poll(0)
        except Exception:
            pass


class PulsarEventBus(InMemoryEventBus):
    def __init__(self, service_url: str, topic: str) -> None:
        super().__init__()
        self.topic = topic
        self._client = None
        self._producer = None
        try:
            import pulsar  # type: ignore

            self._client = pulsar.Client(service_url)
            self._producer = self._client.create_producer(topic)
        except Exception:
            self._producer = None

    def publish(self, event_type: str, envelope: dict[str, Any]) -> None:
        super().publish(event_type, envelope)
        if not self._producer:
            return
        payload = json.dumps({"event_type": event_type, **envelope}, default=str).encode("utf-8")
        try:
            self._producer.send(payload)
        except Exception:
            pass


def build_event_bus() -> EventBus:
    backend = settings.event_bus_backend
    if backend == "kafka" and settings.kafka_bootstrap_servers:
        return KafkaEventBus(settings.kafka_bootstrap_servers, settings.kafka_topic)
    if backend == "pulsar" and settings.pulsar_service_url:
        return PulsarEventBus(settings.pulsar_service_url, settings.pulsar_topic)
    return InMemoryEventBus()
