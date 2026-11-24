---
name: peoplecode-generation
description: Guardrail for PeopleCode coding when built-in verification or syntax-sensitive structure is needed. Use for generating, changing, refactoring, or reviewing PeopleCode that involves built-in functions, unknown signatures, Application Classes/imports, functions, component buffer/rowset access, SQL, or compile-safety validation. Do not use for general PeopleSoft questions or routine PeopleCode discussion with no code to verify.
---

# PeopleCode Generation

Keep this skill lightweight. Verify only what affects the current task; do not let reference work distract from the user's main goal.

## Syntax References

Load `references/syntax/01_core/core_rules_and_quality.md` for PeopleCode creation or modification.

Then load only relevant files:
- Branching/loops: `references/syntax/02_control_flow/control_flow.md`
- Functions/declarations: `references/syntax/03_functions/functions_and_declarations.md`
- Application Classes/imports/objects: `references/syntax/04_objects_and_classes/*.md`
- Rowset/buffer/SQL: `references/syntax/05_data_and_sql/*.md`

## Built-In Verification

Use verified PeopleCode built-ins when they fit. Do not invent names, signatures, return types, or meta-HTML forms.

For any built-in being introduced or changed:

1. Search `references/peoplecode_built_in_functions_by_category.md` with targeted terms: task keywords, synonyms, and technical variants.
2. If search finds no confident candidate, scan the full category file once before deciding no built-in is available.
3. Fetch exact usage only for final built-ins with:

```text
scripts\lookup_peoplecode_reference.bat <exact-name> [<exact-name> ...]
```

Batch names when practical, for example:

```text
scripts\lookup_peoplecode_reference.bat MsgGetText SQLExec CreateRowset
```

Rules:
- Do not load the full built-in category file until targeted search fails.
- Do not inspect the data behind scripts\lookup_peoplecode_reference.bat; use the lookup helper.
- If lookup fails for a required built-in, use a verified alternative or report the blocker.
- Custom logic is acceptable only after search plus the fallback full scan find no suitable built-in, or after verified usage does not match the requirement.

## Done

Before responding, ensure final code matches loaded syntax references, all final built-ins were verified, and any uncertainty is stated plainly.
