"""Stage 4 — is the answer grounded, correct, cited and appropriately silent?

"Correct" is four separable properties, scored independently so a failure can be
attributed rather than guessed at:

    grounded      every quantity in the answer traces back to a retrieved block
    correct       the facts match the gold answer (numeric match + LLM judge)
    attributed    the [N] citations point at blocks that actually support the claim
    silent        an unanswerable question gets a refusal, not an invention

Generation runs through ``build_generator`` — the same wiring the API uses — so
image hydration is exercised exactly as it is in production, and ``--hydration``
turns it on or off for a controlled A/B.
"""
