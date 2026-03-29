---
name: ast-grep
description: Guide for writing ast-grep rules to perform structural code search and analysis. Use when users need to search code with AST-aware patterns, find structural language constructs, or build reusable ast-grep queries that go beyond plain text grep.
---

# ast-grep Code Search

Translate natural-language structural search requests into tested ast-grep rules. Prefer the smallest rule that works, then scale up only when the query actually needs relational or composite logic.

## Process

1. Identify the language, the target structure, and one concrete example of code that should match.
2. Start with `ast-grep run --pattern` for simple node matches.
3. Move to `ast-grep scan --inline-rules` or a rule file only when you need:
   - `kind`
   - `has` or `inside`
   - `all`, `any`, or `not`
4. For relational rules, default to `stopBy: end` unless a narrower boundary is intentional.
5. Test the rule on a tiny example with `--stdin` or a scratch file before searching the real codebase.
6. Use `--json` when another tool or script will consume the results.

## Rule Selection

- Use `pattern` for direct syntax matches such as `console.log($ARG)`.
- Use `kind` plus relational rules for structural queries such as "functions containing await".
- Use `all`, `any`, and `not` when the query combines multiple structural constraints.
- Use `--debug-query=cst`, `--debug-query=ast`, or `--debug-query=pattern` when a rule does not match as expected.

## Command Patterns

Simple pattern search:

```bash
ast-grep run --pattern 'console.log($ARG)' --lang javascript .
```

Inline relational rule:

```bash
ast-grep scan --inline-rules "id: async-await
language: javascript
rule:
  kind: function_declaration
  has:
    pattern: await \$EXPR
    stopBy: end" .
```

Debug the parsed structure:

```bash
ast-grep run --pattern 'class $NAME { $$$BODY }' --lang javascript --debug-query=pattern
```

## CRITICAL

- Escape metavariables in shell-quoted inline rules: use `\$VAR`.
- Validate a rule on an example before using it as a codebase-wide contract.
- Prefer simple rules first. Most broken ast-grep queries are over-specified.
- Load `references/rule_reference.md` when you need full syntax details or less common rule types.
