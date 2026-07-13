export type Message = { role: 'user' | 'assistant'; text: string; ts: string };
export type Thread = { id: string; title: string; updatedAt: string };
export type ChatResult = { answer: string; conversationId: string };
export type ChatSubmit = { jobId: string; conversationId: string };
export type JobStatus = {
  status: 'pending' | 'done' | 'error';
  conversationId: string;
  answer: string;
  error: string;
};
