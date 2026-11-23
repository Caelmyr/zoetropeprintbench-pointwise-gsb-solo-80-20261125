#!/usr/bin/env python3
"""Conservative Python backend physical LOC report; not a semantic padding review."""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import token
import tokenize


EXCLUDED_DIRS = {
    '.git', '.venv', 'venv', 'env', '__pycache__', 'node_modules', 'site-packages',
    'vendor', 'vendors', 'third_party', 'third-party', 'dist', 'build', 'generated',
    'tests', 'test', 'testing', 'scripts', 'tools', 'docs', 'documentation',
    'frontend', 'static', 'templates', 'assets', 'data', 'runtime-data', 'fixtures',
    '.pytest_cache', '.mypy_cache', '.tox', '.cache', '__pypackages__',
}
PUNCTUATION = {'(', ')', '[', ']', '{', '}', ',', ':', ';', '.', '...'}


def effective_lines(data, filename):
    """Ignore bare strings and multiline string bodies; count substantive token lines."""
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    source = data.decode(encoding)
    tree = ast.parse(source, filename=filename)
    bare_strings = set()
    string_interiors = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            value = node.value
            if isinstance(value, ast.JoinedStr) or isinstance(value.value, str):
                bare_strings.update(range(node.lineno, node.end_lineno + 1))
        if isinstance(node, (ast.Constant, ast.JoinedStr)) and node.end_lineno > node.lineno:
            if isinstance(node, ast.JoinedStr) or isinstance(node.value, str):
                string_interiors.update(range(node.lineno + 1, node.end_lineno))
    lines = set()
    for item in tokenize.tokenize(io.BytesIO(data).readline):
        row = item.start[0]
        if row in bare_strings or row in string_interiors:
            continue
        if item.type in (token.NAME, token.NUMBER):
            lines.add(row)
        elif item.type == token.STRING and item.start[0] == item.end[0]:
            lines.add(row)
        elif item.type == token.OP and item.string not in PUNCTUATION:
            lines.add(row)
    return len(lines)


def report(project, source_roots):
    project = project.resolve(strict=True)
    files = {}
    roots = []
    excluded_directories = set()
    for raw in source_roots:
        relative = Path(raw)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('source-root must be project-relative without ..')
        source = project / relative
        if any(p.is_symlink() for p in [source, *source.parents] if p != project.parent):
            raise ValueError('source-root must not use symlinks')
        source = source.resolve(strict=True)
        if not source.is_dir() or not source.is_relative_to(project):
            raise ValueError('source-root must be a directory inside project')
        roots.append(str(relative))
        if set(source.relative_to(project).parts) & EXCLUDED_DIRS:
            excluded_directories.add(str(source.relative_to(project)))
            continue
        for directory, children, filenames in os.walk(source, followlinks=False):
            directory = Path(directory)
            for child in list(children):
                path = directory / child
                if child in EXCLUDED_DIRS or path.is_symlink():
                    children.remove(child)
                    excluded_directories.add(str(path.relative_to(project)))
            for filename in filenames:
                path = directory / filename
                files[str(path.relative_to(project))] = path
    rows = []
    for relative, path in sorted(files.items()):
        parts = Path(relative).parts
        reason = None
        if path.is_symlink():
            reason = 'symlink'
        elif set(parts[:-1]) & EXCLUDED_DIRS:
            reason = 'excluded directory'
        elif path.suffix != '.py':
            reason = 'not Python source'
        elif path.name.startswith(('test_', 'conftest')) or path.name.endswith('_test.py'):
            reason = 'test source'
        data = None if reason else path.read_bytes()
        row = {'path': relative, 'included': reason is None,
               'sha256': hashlib.sha256(data).hexdigest() if data is not None else None,
               'effective_lines': 0, 'exclusion_reason': reason}
        if reason is None:
            row['effective_lines'] = effective_lines(data, relative)
        rows.append(row)
    total = sum(r['effective_lines'] for r in rows)
    return {'language': 'Python only', 'source_roots': roots, 'files': rows,
            'excluded_directories': sorted(excluded_directories),
            'effective_lines': total, 'strictly_over_5000': total > 5000,
            'semantic_padding_review': 'required; not performed by this counter',
            'scope_review': 'source roots must be audited for backend ownership and generated/vendor code'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True)
    parser.add_argument('--source-root', action='append', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = report(args.project_root, args.source_root)
    except (OSError, ValueError, SyntaxError, tokenize.TokenError) as exc:
        parser.exit(1, f'Cannot produce a complete report: {exc}\n')
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(rendered, encoding='utf-8')
    else:
        sys.stdout.write(rendered)
    return 0 if result['strictly_over_5000'] else 2


if __name__ == '__main__':
    sys.exit(main())
