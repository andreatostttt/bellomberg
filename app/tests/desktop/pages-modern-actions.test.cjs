const test = require('node:test');
const assert = require('node:assert/strict');
const { auditVolCanvasReadbackAdvisory, CANVAS2D_READBACK_ADVISORY } = require('./pages-modern-actions.cjs');

const verifiedVolScenario = {
  page: 'vol',
  scenario: 'populated-chain-details-strategy-payoff-and-native-plotly-camera-use-fixtures',
  status: 'passed',
  assertionResults: { livePlotlySurfaceMeshAndWebGLVerified: true, nativeCameraUpdatedAndPreserved: true },
  nativePlotlyCamera: { afterSevenViewportCapture: {
    meshGrid: { rows: 2, columns: 5 },
    selectedCanvas: { contextType: 'webgl', contextVersion: 'WebGL 1.0', contextLost: false },
  } },
};

test('allows only the exact Vol Canvas2D readback advisory after a verified native mesh pass', () => {
  const expected = { page: '/vol', level: 2, message: CANVAS2D_READBACK_ADVISORY };
  const audit = auditVolCanvasReadbackAdvisory([expected], { graphicsMode: 'native', scenarios: [verifiedVolScenario] });
  assert.equal(audit.nativeVolMeshVerified, true);
  assert.deepEqual(audit.expectedCanvasReadbackAdvisories, [expected]);
});

test('keeps unrelated warnings, other pages, wrong levels, and unverified graphics blocking', () => {
  const exact = { page: '/vol', level: 2, message: CANVAS2D_READBACK_ADVISORY };
  const unknown = { page: '/vol', level: 2, message: 'Canvas2D: different warning' };
  const otherPage = { ...exact, page: '/market' };
  const wrongLevel = { ...exact, level: 3 };
  const events = [exact, unknown, otherPage, wrongLevel];
  const audit = auditVolCanvasReadbackAdvisory(events, { graphicsMode: 'native', scenarios: [verifiedVolScenario] });
  assert.deepEqual(audit.expectedCanvasReadbackAdvisories, [exact]);
  for (const event of [unknown, otherPage, wrongLevel]) assert.equal(audit.expectedCanvasReadbackAdvisories.includes(event), false);

  for (const options of [
    { graphicsMode: 'hardware-disabled', scenarios: [verifiedVolScenario] },
    { graphicsMode: 'native', scenarios: [{ ...verifiedVolScenario, status: 'failed' }] },
    { graphicsMode: 'native', scenarios: [{ ...verifiedVolScenario, nativePlotlyCamera: null }] },
    { graphicsMode: 'native', scenarios: [{ ...verifiedVolScenario,
      assertionResults: { livePlotlySurfaceMeshAndWebGLVerified: true, nativeCameraUpdatedAndPreserved: false } }] },
    { graphicsMode: 'native', scenarios: [{ ...verifiedVolScenario,
      assertionResults: { livePlotlySurfaceMeshAndWebGLVerified: true } }] },
  ]) {
    assert.deepEqual(auditVolCanvasReadbackAdvisory([exact], options).expectedCanvasReadbackAdvisories, []);
  }
});
