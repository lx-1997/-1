export const apiGet = jest.fn(() => Promise.resolve(null));

export const apiPost = jest.fn(() => Promise.resolve(null));

export const apiPatch = jest.fn();

export const apiDelete = jest.fn();

export const formatErrorMessage = jest.fn((err: unknown) => String(err));

export const getApiBaseUrls = jest.fn(() => []);