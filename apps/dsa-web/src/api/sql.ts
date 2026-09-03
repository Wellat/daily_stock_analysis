import apiClient from './index';
import { toCamelCase } from './utils';

export type SqlResult = { columns: string[]; rows: unknown[][]; rowCount: number; affectedRows?: number | null; truncated: boolean; statementType: string };
export const sqlApi = {
  getTables: async (): Promise<string[]> => (await apiClient.get<{ tables: string[] }>('/api/v1/sql/tables')).data.tables,
  execute: async (sql: string): Promise<SqlResult> => toCamelCase<SqlResult>((await apiClient.post('/api/v1/sql/execute', { sql })).data),
};
