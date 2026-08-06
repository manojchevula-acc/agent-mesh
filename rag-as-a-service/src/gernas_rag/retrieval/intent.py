"""Decide whether a query wants images at all.

Running the image branch on every query wastes latency and risks polluting
answers for pure-text questions. Explicit request > heuristic > config default.

Deliberately a keyword heuristic rather than a classifier: it is inspectable,
zero-latency, and tunable by editing YAML. An embedding or LLM router would slot
in behind the same interface.
"""

from ..config.multimodal import ImageIntent, MultimodalRetrievalConfig
from ..models.retrieval import RetrieveRequest


class ImageIntentRouter:
    def __init__(self, config: MultimodalRetrievalConfig) -> None:
        self._c = config

    def wants_images(self, request: RetrieveRequest) -> bool:
        # An image query is, by definition, an image search.
        if request.query_image is not None:
            return True
        # Explicit client override beats everything else.
        if request.include_images is not None:
            return request.include_images
        # An explicit modality list is also an override.
        if request.modalities is not None:
            return "image" in request.modalities

        mode = self._c.image_intent
        if mode is ImageIntent.ALWAYS:
            return True
        if mode is ImageIntent.NEVER:
            return False

        query = request.query.lower()
        return any(kw in query for kw in self._c.intent_keywords)
