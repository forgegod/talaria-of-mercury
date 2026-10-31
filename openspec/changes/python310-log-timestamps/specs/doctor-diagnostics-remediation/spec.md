## MODIFIED Requirements

### Requirement: Comma-millisecond log timestamps

Doctor SHALL parse standard comma-millisecond log timestamps
(`YYYY-MM-DD HH:MM:SS,mmm`) on Python 3.10 and newer by normalising the comma
separator before ISO parsing. The interpreter's version-specific
fractional-second support MUST NOT suppress log findings.

#### Scenario: A comma-millisecond warning falls inside the window

- **WHEN** a log line reads `2026-01-02 12:00:00,123 WARNING agent: finish_reason='length'` and the resolved look-back window starts at `2026-01-02 12:00:00` UTC, on Python 3.10 or newer
- **THEN** `truncation_log_markers` counts the line as a length-class hit instead of skipping it as unparsable
- **AND** the hit is attributed to the log file that contains it
- **AND** the same line before the window start is ignored
