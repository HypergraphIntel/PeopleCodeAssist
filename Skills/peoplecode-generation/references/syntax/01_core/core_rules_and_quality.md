# Core Rules and Quality Patterns

Use this file as the default contract when generating PeopleCode. If uncertain, follow these rules exactly.

## Canonical code order (default)
1. `import` statements
2. `Declare Function` statements (if needed)
3. variable/constants declarations (`Local`, `Global`, `Component`, `Constant`)
4. function/class definitions (where applicable)
5. executable statements

## Compile-safety rules
- End statements with `;`.
- Use explicit block terminators: `End-If;`, `End-For;`, `End-While;`, `End-Evaluate;`, `End-function`/`End-Function`.
- Keep keyword/block casing consistent within the same file.
- Prefer explicit, fully qualified references in ambiguous contexts (for example `RECORD.FIELD`, `SCROLL.X`).


## Imports (critical)
- Place all `import` statements at the beginning of the file, before declarations and executable statements.
- Add import before first use of each Application Class type.
- Keep imported class path and instantiated/referenced class path identical.
- For interface-based usage, import both interface and concrete implementation when both are referenced.

Example:
```peoplecode
import MY_PACKAGE:MY_CLASS;

Local MY_PACKAGE:MY_CLASS &myObj = create MY_PACKAGE:MY_CLASS();
&myObj.myFunction();
```

## Functions and declarations
- External PeopleCode functions must be declared before use.
- Place function declarations/definitions near the top, before executable body logic.
- Use explicit parameter typing (`As`) and `Returns` when possible for clarity and safer generation.
- Remember: PeopleCode function parameters are passed by reference for internal/external PeopleCode functions.

## Assignment, expressions, and comments
- Assignment form: `&var = expression;`
- `=` can mean assignment or comparison by context; keep conditions explicit.
- Object assignment is reference-based; scalar assignment is value-based.
- `Rem ...;` requires `;`.
- `/* ... */` and `<* ... *>` are valid comment forms.
- Do not use compiler-generated `/+ +/` annotations as normal comments.

## Event and transaction safety
- Respect think-time function restrictions by event.
- Do not issue manual SQL `Commit`/`Rollback` via `SQLExec` in component processing.

## High-impact quality patterns
- Add `Break;` in `Evaluate` when only one `When` should execute.
- Isolate repeated expressions outside loops.
- Reuse `SQL` objects in loops instead of repeated one-off SQL parsing.
- Avoid large `Fill()` usage when row volume is uncertain.
- Set long-lived object references to `Null` when done.
- Prefer simple properties over get/set-backed properties unless behavior requires get/set.

## Pre-save checklist (must pass)
- Imports are at top and complete.
- No undefined identifiers from missing declarations/imports.
- No placeholder text remains.
- All blocks are properly closed with valid terminators.
- Function calls/signatures are consistent (name, params, return usage).
- SQL uses bind-safe patterns where possible.
- Code is ready for save+compile loop without structural edits.
