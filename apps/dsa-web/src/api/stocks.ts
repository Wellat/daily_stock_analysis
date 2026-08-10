import apiClient from './index';

export type ExtractItem = {
  code?: string | null;
  name?: string | null;
  confidence: string;
};

export type ExtractFromImageResponse = {
  codes: string[];
  items?: ExtractItem[];
  rawText?: string;
};

export const stocksApi = {
  async extractFromImage(file: File): Promise<ExtractFromImageResponse> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
    const response = await apiClient.post(
      '/api/v1/stocks/extract-from-image',
      formData,
      {
        headers,
        timeout: 60000, // Vision API can be slow; 60s
      },
    );

    const data = response.data as { codes?: string[]; items?: ExtractItem[]; raw_text?: string };
    return {
      codes: data.codes ?? [],
      items: data.items,
      rawText: data.raw_text,
    };
  },

  async parseImport(file?: File, text?: string): Promise<ExtractFromImageResponse> {
    if (file) {
      const formData = new FormData();
      formData.append('file', file);
      const headers: { [key: string]: string | undefined } = { 'Content-Type': undefined };
      const response = await apiClient.post('/api/v1/stocks/parse-import', formData, { headers });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    if (text) {
      const response = await apiClient.post('/api/v1/stocks/parse-import', { text });
      const data = response.data as { codes?: string[]; items?: ExtractItem[] };
      return { codes: data.codes ?? [], items: data.items };
    }
    throw new Error('请提供文件或粘贴文本');
  },
};

export type StockListItem = {
  code: string;
  instrument_type: string;
  latest_date?: string | null;
  latest_close?: number | null;
};

export type StockDailyBarItem = {
  date?: string | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close?: number | null;
  volume?: number | null;
  amount?: number | null;
  data_source?: string | null;
};

export const marketStocksApi = {
  async listStocks(params: { keyword?: string; page?: number; limit?: number } = {}) {
    const { data } = await apiClient.get<{ total: number; page: number; limit: number; items: StockListItem[] }>('/api/v1/stocks/list', { params });
    return data;
  },
  async getStockBars(code: string, params: { start_date?: string; end_date?: string; limit?: number } = {}) {
    const { data } = await apiClient.get<{ code: string; total: number; items: StockDailyBarItem[] }>(`/api/v1/stocks/${encodeURIComponent(code)}/bars`, { params });
    return data;
  },
};
