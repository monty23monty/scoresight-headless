// Run with: node --test tests/web/operator.test.cjs
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

function harness() {
  const canvases = [];
  class Element {
    constructor() { this.children = []; this.style = {}; }
    append(...nodes) { nodes.forEach((node) => this.insertBefore(node, null)); }
    insertBefore(node, next) {
      node.remove();
      this.children.splice(next ? this.children.indexOf(next) : this.children.length, 0, node);
      node.parent = this;
    }
    remove() {
      if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1);
      this.parent = null;
    }
    setAttribute() {}
    addEventListener() {}
    getContext() { return {fillRect() {}, drawImage() {}, clearRect() {}}; }
  }
  const nodes = new Map();
  const sockets = [];
  const decodes = [];
  const scope = vm.createContext({
    document: {
      querySelector: () => ({content: '', textContent: '{"regions":[]}'}),
      getElementById: (id) => {
        if (!nodes.has(id)) nodes.set(id, new Element());
        return nodes.get(id);
      },
      createElement: (tag) => {
        const element = new Element();
        if (tag === 'canvas') canvases.push(element);
        return element;
      },
    },
    location: {protocol: 'http:', host: 'localhost'},
    WebSocket: class {
      static OPEN = 1;
      constructor() { this.readyState = 1; sockets.push(this); }
    },
    createImageBitmap: () => new Promise((resolve, reject) => decodes.push({resolve, reject})),
  });
  const source = readFileSync(resolve(__dirname, '../../src/scoresight/web/static/operator.js'), 'utf8');
  vm.runInContext(source.slice(0, source.lastIndexOf('\nbindConfig();')), scope);
  return {run: (code) => vm.runInContext(code, scope), canvases, nodes, sockets, decodes};
}

function bitmap() {
  return {width: 960, height: 540, closed: false, close() { this.closed = true; }};
}

test('20,000 result updates reuse canvas backing stores and release removed rows', () => {
  const h = harness();
  h.run(`
    const fields = ['a', 'b', 'c'].map(id => ({id, name: id, value: '0', state: 'ok'}));
    acceptedPreviews.set('a', {width: 100, height: 50});
    for (let i = 0; i < 20000; i++) {
      fields[0].value = String(i);
      renderResults(fields);
    }
  `);
  assert.equal(h.canvases.length, 3);
  assert.equal(h.nodes.get('results').children[0].children[2].textContent, '19999');
  h.run('renderResults([...fields].reverse())');
  assert.equal(h.nodes.get('results').children[0].children[1].textContent, 'c');
  h.run('renderResults([])');
  assert.equal(h.nodes.get('results').children.length, 0);
  assert.ok(h.canvases.every(canvas => canvas.width === 0 && canvas.height === 0));
});

test('slow preview decoding drops excess frames and recovers from decode failures', async () => {
  const h = harness();
  h.run('connectPreview()');
  const socket = h.sockets[0];
  const first = socket.onmessage({data: {}});
  for (let i = 0; i < 1000; i++) await socket.onmessage({data: {}});
  assert.equal(h.decodes.length, 1);
  h.decodes[0].reject(new Error('invalid image'));
  await first;
  const next = socket.onmessage({data: {}});
  const frame = bitmap();
  h.decodes[1].resolve(frame);
  await next;
  const replacement = socket.onmessage({data: {}});
  h.decodes[2].resolve(bitmap());
  await replacement;
  assert.equal(frame.closed, true);
  const disconnected = socket.onmessage({data: {}});
  socket.readyState = 3;
  const stale = bitmap();
  h.decodes[3].resolve(stale);
  await disconnected;
  assert.equal(stale.closed, true);
});

test('accepted snapshots are bounded per region and released if the region is deleted', async () => {
  const h = harness();
  h.run(`
    previewBitmap = {width: 960, height: 540};
    config.regions = [{id: 'a', rect: {x: 0, y: 0, width: .5, height: .5}}];
  `);
  const first = h.run("captureAcceptedPreview({id: 'a'})");
  for (let i = 0; i < 1000; i++) await h.run("captureAcceptedPreview({id: 'a'})");
  assert.equal(h.decodes.length, 1);
  h.run('config.regions = []; pruneAcceptedPreviews()');
  const stale = bitmap();
  h.decodes[0].resolve(stale);
  assert.equal(await first, false);
  assert.equal(stale.closed, true);
  assert.equal(h.run('acceptedPreviews.size'), 0);
  assert.equal(h.run('acceptedPreviewCaptures.size'), 0);
});
