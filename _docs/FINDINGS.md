# Design findings and limitations

This public summary preserves the practical design lessons from the source workflow; private benchmarks, client examples, and raw run logs are not distributed.

- Align recorders first, then place camera clips on that shared clock. Pauses and split recordings need session handling; do not infer alignment from device dates.
- Use multiple waveform checks and retain an explicit review state. A silent camera track cannot provide reliable audio sync evidence.
- Transcribe full recorders once and slice their transcripts into each clip's time range. Camera scratch audio often misses the dialogue the editor needs.
- Description combines deterministic technical metadata with sampled frames and a second visual verification pass. Dark footage, small text, fast action, and events between sampled frames remain difficult.
- Use human shoot notes for context. Do not treat a model's inferred speech, identity, rights, or camera specifications as ground truth.
- Preserve human review, rights, names, and usage fields across reruns.
- Long takes require more frames and tokens. The inherited pipeline reduces effort for very long clips; inspect run logs and provider limits.
- Request-file mode separates local preparation from the agent's visual work. It still consumes the agent plan's usage, even when API cost in the run log is zero.

Model defaults and historical estimates remain in the skill source for traceability. They are not a promise of current availability, cost, processing time, or quality. Validate on a small representative batch before processing a full collection.
