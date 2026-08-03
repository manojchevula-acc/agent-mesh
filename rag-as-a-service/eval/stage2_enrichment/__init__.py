"""Stage 2 — are the captions right, and is each media chunk stored correctly?

Two independent questions, deliberately kept as two separate scorers:

``integrity``  Did every captured image become exactly one resolvable, correctly
               keyed and correctly linked chunk? This is where a figure can be
               captured, stored as an artifact, fail captioning and then vanish
               from the index with no error raised anywhere.

``captions``   Does the VLM transcription actually match what is printed on the
               image? Scored against human transcriptions, because scoring it
               against the caption itself would be circular — and because a
               wrong caption produces answers that are fluent, cited and false.
"""
