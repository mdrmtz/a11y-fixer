# A11y Fixer Skill

This skill provides a standardized workflow for resolving accessibility (a11y) violations based on the axe-core rules and WCAG 2.2 guidelines.

## Steps

1. **Identify WCAG Success Criterion**: Map the axe-core rule to the corresponding WCAG 2.2 success criterion.
2. **Query WCAG Techniques**: Use the `wcag-mcp` tool to retrieve relevant sufficient, advisory, and failure techniques for the identified success criterion.
3. **Validate Against Anti-Patterns**: Check the proposed fix against any framework-specific anti-patterns or known issues.
4. **Construct Fix Candidate**: Assemble the final `ViolationResponse` object with the fix details.

## Example Workflow

```python
# Step 1: Map axe-core rule to WCAG criterion
rule_id = 'color-contrast'
criterion_ref_id = '1.4.3'

# Step 2: Query WCAG techniques
<function_calls>
<invoke name="get-success-criteria-detail">
<parameter name="ref_id">1.4.3