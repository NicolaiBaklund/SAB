# Copilot Instructions

## Project goals

This repository is for a sentiment analysis system for Norwegian companies.
Favor clear, maintainable, modular code over clever shortcuts.

## Engineering rules

- Prefer test-driven development.
- Write small, focused functions and modules with single responsibilities.
- Keep code explicit and easy to read.
- Avoid premature abstractions, hidden coupling, and overengineering.
- Do not add optional features unless they are needed for the requested task.
- Preserve domain terms and business meaning in names.

## Testing rules

- Add or update tests for every behavior change.
- Prefer fast, deterministic tests.
- Keep fixtures and test data minimal.
- If a change is hard to test, simplify the design first.

## Implementation style

- Make dependencies clear at module boundaries.
- Use composition over deep inheritance.
- Separate domain logic from I/O, framework, and infrastructure code.
- Keep functions and files small enough to scan quickly.
- When there is a simpler correct solution, choose it.

## Review mindset

- Optimize for correctness, readability, and long-term maintainability.
- Do not hide missing behavior behind placeholders or temporary shortcuts.
- Prefer complete, boring solutions that another engineer can understand quickly.