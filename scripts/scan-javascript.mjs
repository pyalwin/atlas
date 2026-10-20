// Parse source text supplied by Atlas. This never imports or executes repository code.
import { parse } from '@babel/parser';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const files = JSON.parse(input);
const results = files.map(({path, source}) => {
  const symbols = [], imports = [], counts = new Map();
  const fileId = `file:${path}`;
  try {
    const tree = parse(source, {
      sourceType: 'unambiguous', errorRecovery: false,
      plugins: [/\.(?:[mc]?ts|tsx)$/.test(path) ? 'typescript' : null,
        /\.(?:jsx|tsx|js)$/.test(path) ? 'jsx' : null, 'decorators-legacy'].filter(Boolean),
    });
    const comment = node => (node.leadingComments || []).filter(c => c.type === 'CommentBlock')
      .map(c => c.value.replace(/^\s*\*\s?/gm, '').trim()).filter(Boolean).at(-1)?.split(/\n\s*\n/)[0].slice(0, 300) || '';
    const walk = (node, parent = fileId, names = [], hint = null, doc = '') => {
      if (!node || typeof node !== 'object' || typeof node.type !== 'string') return;
      if (['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration'].includes(node.type) && node.source) {
        imports.push({specifier: node.source.value, line: node.loc.start.line});
      }
      if (node.type === 'TSImportEqualsDeclaration' && node.moduleReference?.expression?.type === 'StringLiteral') {
        imports.push({specifier: node.moduleReference.expression.value, line: node.loc.start.line});
      }
      if (node.type === 'ImportExpression' && node.source?.type === 'StringLiteral') {
        imports.push({specifier: node.source.value, line: node.loc.start.line});
      }
      if (node.type === 'CallExpression' && node.callee?.type === 'Import' && node.arguments[0]?.type === 'StringLiteral') {
        imports.push({specifier: node.arguments[0].value, line: node.loc.start.line});
      }
      if (node.type === 'VariableDeclarator') {
        walk(node.init, parent, names, node.id?.type === 'Identifier' ? node.id.name : null, doc || comment(node));
        return;
      }
      if (['ClassProperty', 'ClassPrivateProperty', 'ObjectProperty'].includes(node.type) && !node.computed) {
        const propertyName = node.key?.type === 'PrivateName' ? `#${node.key.id.name}` : node.key?.name || node.key?.value;
        if (typeof propertyName === 'string') {
          walk(node.value, parent, names, propertyName, comment(node));
          return;
        }
      }
      if (node.type === 'ExportDefaultDeclaration') {
        walk(node.declaration, parent, names, 'default', comment(node)); return;
      }
      const isClass = ['ClassDeclaration', 'ClassExpression'].includes(node.type);
      const isFunction = ['FunctionDeclaration', 'FunctionExpression', 'ArrowFunctionExpression',
        'ClassMethod', 'ClassPrivateMethod', 'ObjectMethod', 'TSDeclareFunction'].includes(node.type);
      const name = node.id?.name || hint || (!node.computed && (node.key?.name || node.key?.id?.name));
      let nextParent = parent, nextNames = names;
      if ((isClass || isFunction) && name) {
        const kind = isClass ? 'class' : 'function';
        const qualified = [...names, name].join('.');
        const base = `${kind}:${path}:${qualified}`;
        const count = (counts.get(base) || 0) + 1; counts.set(base, count);
        const id = count === 1 ? base : `${base}#${count}`;
        symbols.push({id, name, kind, parentId: parent, path, line: node.loc.start.line,
          endLine: node.loc.end.line, summary: comment(node) || doc});
        nextParent = id; nextNames = [...names, name];
      }
      for (const [key, value] of Object.entries(node)) {
        if (['loc', 'tokens', 'comments', 'leadingComments', 'trailingComments', 'innerComments', 'extra'].includes(key)) continue;
        const inheritedDoc = key === 'declarations' || key === 'declaration' ? comment(node) || doc : '';
        if (Array.isArray(value)) value.forEach(child => walk(child, nextParent, nextNames, null, inheritedDoc));
        else if (value && typeof value === 'object') walk(value, nextParent, nextNames, null, inheritedDoc);
      }
    };
    walk(tree.program);
    return {path, symbols, imports};
  } catch (error) {
    return {path, symbols: [], imports: [], error: `Could not parse ${path}${error.loc?.line ? ` at line ${error.loc.line}` : ''}.`};
  }
});
process.stdout.write(JSON.stringify(results));
