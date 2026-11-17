"""Read-only structural inspection; never executes the inspected JavaScript.

Resolves the simple rotated string tables seen in this installed build.
Alias replacement is a navigation aid, not a complete scope-aware decompiler.
No app files are changed. Only bounded structural excerpts are printed.
"""
import ast
import json
import operator
from pathlib import Path
import re
import sys


def arithmetic(node):
    if isinstance(node, ast.Expression):
        return arithmetic(node.body)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return arithmetic(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
    if isinstance(node, ast.BinOp):
        operations = {ast.Add: operator.add, ast.Sub: operator.sub,
                      ast.Mult: operator.mul, ast.Div: operator.truediv}
        if type(node.op) in operations:
            return operations[type(node.op)](arithmetic(node.left), arithmetic(node.right))
    raise ValueError('Unexpected arithmetic syntax')


def resolve(path):
    source = path.read_text()
    table_match = re.search(r'function\s+(\w+)\(\)\{(?:var|const)\s+(\w+)=(\[(?:\s*\'(?:\\.|[^\'\\])*\'\s*,?)*\]);', source)
    if not table_match:
        raise ValueError('Single string array not found')
    table = ast.literal_eval(table_match[3])
    decoder_match = re.search(r'function\s+(\w+)\([^)]*\)\{.{0,220}?\w+=\w+-(0x[0-9a-f]+);.{0,90}?' + re.escape(table_match[1]) + r'\(\)', source)
    if not decoder_match:
        # This layout initializes the table before declaring the decoder closure.
        decoder_match = re.search(r'function\s+(\w+)\([^)]*\)\{.{0,100}?' + re.escape(table_match[1]) + r'\(\);.{0,160}?\w+=\w+-(0x[0-9a-f]+);', source)
    if not decoder_match:
        raise ValueError('Simple decoder not found')
    decoder, offset = decoder_match[1], int(decoder_match[2], 16)
    rot = re.search(r'while\(!!\[\]\)\{try\{(?:var|const)\s+\w+=(.*?);if\(', source)
    target_match = re.search(re.escape(table_match[1]) + r',\s*(0x[0-9a-f]+)\)', source)
    if not rot or not target_match:
        raise ValueError('Rotation arithmetic not found')
    target = int(target_match[1], 16)
    def number(match, shift):
        value = table[(int(match[1], 16) - offset + shift) % len(table)]
        prefix = re.match(r'\s*([+-]?\d+)', value)
        if not prefix:
            raise ValueError('NaN')
        return prefix[1]
    for shift in range(len(table)):
        try:
            expression = re.sub(r'parseInt\(\w+\((0x[0-9a-f]+)\)\)', lambda m: number(m, shift), rot[1])
            value = arithmetic(ast.parse(expression, mode='eval'))
            if value == target:
                break
        except (ValueError, ZeroDivisionError):
            pass
    else:
        raise ValueError('Rotation did not resolve')
    table = table[shift:] + table[:shift]
    aliases = {decoder}
    while True:
        added = {m[1] for m in re.finditer(r'\b(\w+)=(\w+)(?=[,;])', source) if m[2] in aliases}
        if added <= aliases:
            break
        aliases |= added
    # Remove the data table from excerpts so matches represent references in code.
    code = source[:table_match.start(3)] + '[]' + source[table_match.end(3):]
    replacements = 0
    def replace(match):
        nonlocal replacements
        if match[1] not in aliases:
            return match[0]
        index = int(match[2], 16) - offset
        if not 0 <= index < len(table):
            return match[0]
        replacements += 1
        return json.dumps(table[index], ensure_ascii=False)
    code = re.sub(r'\b(\w+)\((0x[0-9a-f]+)\)', replace, code)
    return code, {'file': str(path), 'strings': len(table), 'rotation': shift,
                  'aliases': len(aliases), 'replacements': replacements}


if __name__ == '__main__':
    code, info = resolve(Path(sys.argv[1]))
    print(json.dumps(info, ensure_ascii=False))
    for term in sys.argv[2:]:
        print('\nREFERENCE:', term)
        positions = [m.start() for m in re.finditer(re.escape(term), code)]
        print('count:', len(positions))
        for pos in positions[:3]:
            print(code[max(0,pos-160):pos+320])
