const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {test} = require("node:test");
const vm = require("node:vm");

async function dashboard(failSecondBatch = false) {
  const nodes = new Map();
  const requests = [];
  const context = vm.createContext({
    document: {
      querySelector(selector) {
        if (!nodes.has(selector)) {
          nodes.set(selector, {textContent: "", addEventListener() {}, setAttribute() {}});
        }
        return nodes.get(selector);
      },
      querySelectorAll() { return []; }
    },
    async fetch(url, options) {
      if (url === "/api/config") return {ok: true, json: async () => ({source: "Test fixture"})};
      if (url === "/api/companies") return {ok: true, json: async () => ({companies: []})};
      const numbers = JSON.parse(options.body).numbers;
      requests.push({url, numbers});
      if (failSecondBatch && requests.length === 2) throw new Error("Connection lost");
      return {
        ok: true,
        json: async () => ({
          results: numbers.map(number => ({
            registration_number: number, status: "ok", facts: {}, history: []
          }))
        })
      };
    }
  });
  const source = fs.readFileSync(path.join(__dirname, "../signalpost/static/app.js"), "utf8");
  vm.runInContext(`${source}\nrender = () => {};`, context);
  await new Promise(setImmediate);
  return {context, requests, notice: context.document.querySelector("#notice")};
}

test("refresh saved profiles batches more than 100 companies and merges every result", async () => {
  const {context, requests, notice} = await dashboard();
  await vm.runInContext("run(Array.from({length: 201}, (_, i) => String(i)), true)", context);
  assert.deepEqual(requests.map(request => request.numbers.length), [100, 100, 1]);
  assert.ok(requests.every(request => request.url === "/api/refresh"));
  assert.equal(vm.runInContext("profiles.length", context), 201);
  assert.equal(vm.runInContext("busy", context), false);
  assert.match(notice.textContent, /Finished 201 lookups: 201 verified/);
});

test("a later batch failure preserves completed refresh results and unlocks the UI", async () => {
  const {context, requests, notice} = await dashboard(true);
  await vm.runInContext("run(Array.from({length: 201}, (_, i) => String(i)), true)", context);
  assert.equal(requests.length, 2);
  assert.equal(vm.runInContext("profiles.length", context), 100);
  assert.equal(vm.runInContext("busy", context), false);
  assert.match(notice.textContent, /Connection lost.*Completed 100 lookups.*not been cleared/);
});

test("ordinary lookup rejects more than 100 inputs without a request", async () => {
  const {context, requests, notice} = await dashboard();
  await vm.runInContext("run(Array.from({length: 101}, (_, i) => String(i)))", context);
  assert.equal(requests.length, 0);
  assert.match(notice.textContent, /at most 100/);
});
