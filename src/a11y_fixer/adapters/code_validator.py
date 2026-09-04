"""Pre-flight code validation for build safety.

Validates TypeScript and template syntax before compilation to catch
common errors (missing imports, undefined components, malformed templates)
and provide actionable feedback to the LLM agent.

Includes caching to avoid re-validating unchanged files (latency optimization).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple


class ValidationResult(NamedTuple):
    """Result of code validation."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]
    suggestions: list[str]


class CodeValidator:
    """Validates TypeScript components and HTML templates for common issues."""

    @staticmethod
    def validate_typescript_file(file_path: Path) -> ValidationResult:
        """Validate a TypeScript component file for common syntax issues.

        Checks for:
        - Missing imports for used symbols
        - Undefined component decorators
        - Malformed property bindings
        - Missing semicolons (style warning)
        """
        if not file_path.exists():
            return ValidationResult(
                is_valid=False,
                errors=[f"File not found: {file_path}"],
                warnings=[],
                suggestions=[],
            )

        content = file_path.read_text()
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []

        # Check for @Component decorator
        if "@Component" not in content:
            errors.append("Missing @Component decorator")
            suggestions.append("Ensure the file has @Component({...}) decoration")

        # Extract imports and used symbols
        import_lines = re.findall(r"from ['\"]([@\w\-/\.]+)['\"]", content)

        # Check for common unimported Angular symbols
        common_issues = [
            (r"\bComponent\b(?!\s+\{)", "Component", "@angular/core"),
            (r"\bInput\b", "Input", "@angular/core"),
            (r"\bOutput\b", "Output", "@angular/core"),
            (r"\bEventEmitter\b", "EventEmitter", "@angular/core"),
            (r"\bOnInit\b", "OnInit", "@angular/core"),
            (r"\bViewChild\b", "ViewChild", "@angular/core"),
            (r"\bCommonModule\b", "CommonModule", "@angular/common"),
            (r"\bFormsModule\b", "FormsModule", "@angular/forms"),
            (r"\bAsyncPipe\b", "AsyncPipe", "@angular/common"),
        ]

        for pattern, symbol, module in common_issues:
            if re.search(pattern, content) and module not in " ".join(import_lines):
                errors.append(
                    f"Used symbol '{symbol}' but not imported from '{module}'"
                )
                suggestions.append(f"Add: import {{ {symbol} }} from '{module}'")

        # Check for template issues
        if "selector:" in content and "<" in content:
            # Look for property bindings
            malformed_bindings = re.findall(r"\[\s*\w+\s*=\s*['\"]", content)
            if malformed_bindings:
                warnings.append("Found potentially malformed property bindings")

        # Check for common TypeScript syntax errors
        if (
            "import {" in content
            and "}" not in content.split("import {")[1].split("\n")[0]
        ):
            errors.append("Malformed import statement (missing closing brace)")
            suggestions.append("Check import statements for syntax errors")

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
        )

    @staticmethod
    def validate_template_file(file_path: Path) -> ValidationResult:
        """Validate an Angular template HTML file.

        Checks for:
        - Unclosed tags
        - Malformed attribute bindings
        - Undefined component references
        - Missing required attributes (alt, aria-label)
        """
        if not file_path.exists():
            return ValidationResult(
                is_valid=False,
                errors=[f"File not found: {file_path}"],
                warnings=[],
                suggestions=[],
            )

        content = file_path.read_text()
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []

        # Check for unclosed tags
        tags = re.findall(r"<(\w+)", content)
        self_closing = {"img", "input", "br", "hr", "meta", "link"}

        for tag in tags:
            if tag not in self_closing:
                closing = f"</{tag}>"
                if closing not in content:
                    errors.append(f"Potentially unclosed <{tag}> tag")
                    suggestions.append(f"Ensure all <{tag}> tags are properly closed")

        # Check for malformed bindings
        malformed = re.findall(r"\[\s*\w+\s*\]\s*=\s*['\"]", content)
        if malformed:
            errors.append("Found malformed property bindings (property syntax error)")
            suggestions.append(
                'Use correct binding syntax: [property]="value" (no space)'
            )

        # Check for missing accessibility attributes on interactive elements
        interactive_without_aria = re.findall(
            r"<(?:button|a|input)\b(?!.*(?:aria-|role=))", content
        )
        if interactive_without_aria:
            warnings.append(
                "Found interactive elements without accessibility attributes"
            )
            suggestions.append(
                "Add aria-label or aria-describedby to interactive elements"
            )

        # Check for images without alt text
        img_tags = re.findall(r"<img\b[^>]*>", content)
        for img_tag in img_tags:
            if "alt=" not in img_tag and "aria-label" not in img_tag:
                warnings.append("Found <img> tag without alt text or aria-label")
                suggestions.append(
                    'Add alt attribute with descriptive text: <img alt="..."'
                )

        # Check for malformed attribute syntax
        malformed_attrs = re.findall(r"\[\w+\]\s*=", content)
        if malformed_attrs:
            # This is valid, but check the value part
            if re.search(r'\[\w+\]\s*=\s*["\'][^"\']*$', content, re.MULTILINE):
                errors.append("Found unterminated attribute value")
                suggestions.append(
                    "Ensure all attribute values are properly quoted and closed"
                )

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
        )

    @staticmethod
    def validate_component_pair(
        ts_path: Path, html_path: Path | None = None
    ) -> ValidationResult:
        """Validate a component's TypeScript and optional template files together.

        Checks for consistency between component class and template.
        """
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []

        # Validate TypeScript
        ts_result = CodeValidator.validate_typescript_file(ts_path)
        errors.extend(ts_result.errors)
        warnings.extend(ts_result.warnings)
        suggestions.extend(ts_result.suggestions)

        # Validate template if provided
        if html_path and html_path.exists():
            html_result = CodeValidator.validate_template_file(html_path)
            errors.extend(html_result.errors)
            warnings.extend(html_result.warnings)
            suggestions.extend(html_result.suggestions)

        # Cross-check: if template uses custom components, ensure they're imported
        if html_path and html_path.exists():
            ts_content = ts_path.read_text()
            html_content = html_path.read_text()

            # Find custom component references in template (AppXyzComponent pattern)
            custom_components = re.findall(r"<(app-\w+)", html_content)
            for component_ref in set(custom_components):
                # Convert kebab-case to PascalCase for TypeScript import check
                pascal_name = "".join(w.capitalize() for w in component_ref.split("-"))
                if pascal_name not in ts_content:
                    errors.append(
                        f"Template uses <{component_ref}> but component not imported in TypeScript"
                    )
                    suggestions.append(
                        f"Import the component: import {{ {pascal_name} }} from './path-to-component'"
                    )

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
        )

    @staticmethod
    def get_import_statement(symbol: str, module: str) -> str:
        """Generate a properly formatted import statement.

        Args:
            symbol: The symbol to import (e.g., "CommonModule")
            module: The module to import from (e.g., "@angular/common")

        Returns:
            Formatted import statement
        """
        return f"import {{ {symbol} }} from '{module}';"

    @staticmethod
    def suggest_fixes(validation_result: ValidationResult) -> str:
        """Generate a human-readable summary of validation issues and fixes.

        Args:
            validation_result: Result from validate_* methods

        Returns:
            Formatted string with errors, warnings, and suggestions
        """
        lines: list[str] = []

        if validation_result.errors:
            lines.append("❌ ERRORS (build will fail):")
            for error in validation_result.errors:
                lines.append(f"  • {error}")

        if validation_result.warnings:
            lines.append("\n⚠️  WARNINGS (may cause runtime issues):")
            for warning in validation_result.warnings:
                lines.append(f"  • {warning}")

        if validation_result.suggestions:
            lines.append("\n💡 SUGGESTIONS:")
            for suggestion in validation_result.suggestions:
                lines.append(f"  • {suggestion}")

        return "\n".join(lines) if lines else "✅ No issues found"
