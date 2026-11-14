import { afterEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { api, EXAMPLE_QUESTIONS } from '../src/api.js';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

describe('api client', () => {
  it('sends JSON requests and returns a successful response', async () => {
    let captured;
    globalThis.fetch = async (path, options) => {
      captured = { path, options };
      return new Response(JSON.stringify({ id: 'synthetic-session' }), {
        status: 201, headers: { 'Content-Type': 'application/json' },
      });
    };

    const result = await api('/api/sessions', 'POST', {});
    assert.deepEqual(result, { id: 'synthetic-session' });
    assert.equal(captured.path, '/api/sessions');
    assert.equal(captured.options.method, 'POST');
    assert.equal(captured.options.body, '{}');
    assert.equal(captured.options.cache, 'no-store');
  });

  it('rejects an unsuccessful API response and preserves the server message', async () => {
    globalThis.fetch = async () => new Response(JSON.stringify({
      detail: { message: 'Consent is required', draft: {} },
    }), { status: 422, headers: { 'Content-Type': 'application/json' } });

    await assert.rejects(api('/api/sessions/synthetic/lead', 'POST', { consent: false }), (error) => {
      assert.equal(error.message, 'Consent is required');
      assert.equal(error.status, 422);
      assert.deepEqual(error.detail, { message: 'Consent is required', draft: {} });
      return true;
    });
  });

  it('uses an HTTP status message when the error body is not JSON', async () => {
    globalThis.fetch = async () => new Response('unavailable', { status: 503 });
    await assert.rejects(api('/api/sessions/synthetic/messages', 'POST', { text: 'test' }), (error) => {
      assert.equal(error.message, 'Ошибка 503');
      assert.equal(error.status, 503);
      return true;
    });
  });

  it('uses generic sample questions without embedded product claims', () => {
    assert.equal(EXAMPLE_QUESTIONS.length, 3);
    assert.ok(EXAMPLE_QUESTIONS.every((question) => question.length > 10));
    assert.ok(EXAMPLE_QUESTIONS.every((question) => !/\b\d+\s*(?:кВт|kW)\b/i.test(question)));
  });
});
