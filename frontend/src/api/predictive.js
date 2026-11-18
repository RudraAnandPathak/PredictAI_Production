// Thin wrappers over the predictive-modelling endpoints. Components never build URLs or read raw envelopes; every call returns the unwrapped `data`.
// Every figure comes from the server's modelling engine. Nothing in the browser trains, scores or explains a model.
import { queryString } from './workspace.js';

const id = encodeURIComponent;
const LONG = 3 * 60 * 1000;

export const predictiveApi = (client) => {
  const data = (promise) => promise.then((r) => r.data);
  const post = (path, body, timeoutMs = LONG) => data(client.request(path, { method: 'POST', body: body ?? {}, timeoutMs }));
  return {
    catalog: () => data(client.request('/ml/catalog')),
    schema: (assetId, params) => data(client.request(`/assets/${id(assetId)}/ml/schema${queryString(params)}`)),
    assess: (assetId, config, versionId) => post(`/assets/${id(assetId)}/ml/assess`, { config, version_id: versionId ?? null }),
    startTraining: (assetId, config, { versionId, label } = {}) => post(`/assets/${id(assetId)}/ml/jobs`, { config, version_id: versionId ?? null, label: label || null }, 60000),
    jobs: (assetId, params) => data(client.request(`/assets/${id(assetId)}/ml/jobs${queryString(params)}`)),
    job: (jobId) => data(client.request(`/ml/jobs/${id(jobId)}`, { timeoutMs: 30000 })),
    cancelJob: (jobId) => post(`/ml/jobs/${id(jobId)}/cancel`, {}, 30000),
    models: (params) => data(client.request(`/ml/models${queryString(params)}`)),
    model: (modelId) => data(client.request(`/ml/models/${id(modelId)}`)),
    renameModel: (modelId, name) => data(client.request(`/ml/models/${id(modelId)}`, { method: 'PATCH', body: { name }, timeoutMs: 30000 })),
    deleteModel: (modelId) => data(client.request(`/ml/models/${id(modelId)}`, { method: 'DELETE', timeoutMs: 60000 })),
    report: (modelId) => data(client.request(`/ml/models/${id(modelId)}/report`, { timeoutMs: 60000 })),
    partialDependence: (modelId, feature) => post(`/ml/models/${id(modelId)}/partial-dependence`, { feature }, 90000),
    compare: (ids) => post('/ml/models/compare', { ids }, 60000),
    whatIf: (modelId, values, scenarios) => post(`/ml/models/${id(modelId)}/what-if`, { values, scenarios }, 120000),
    predict: (modelId, values, { explain = true, save = true } = {}) => post(`/ml/models/${id(modelId)}/predict`, { values, explain, save }, 90000),
    predictBatch: (modelId, datasetAssetId, versionId) => post(`/ml/models/${id(modelId)}/predict-batch`, { dataset_asset_id: datasetAssetId, version_id: versionId ?? null }, 5 * 60 * 1000),
    predictions: (modelId, params) => data(client.request(`/ml/models/${id(modelId)}/predictions${queryString(params)}`)),
    prediction: (predictionId) => data(client.request(`/ml/predictions/${id(predictionId)}`)),
    downloadPrediction: (predictionId) => client.download(`/ml/predictions/${id(predictionId)}/download`, { method: 'GET' }),
  };
};
