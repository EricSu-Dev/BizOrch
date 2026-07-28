import { apiClient } from './client'
import type { Ticket } from './contracts'

export async function listTickets(): Promise<Ticket[]> {
  const { data } = await apiClient.get<Ticket[]>('/tickets')
  return data
}

export async function getTicket(ticketId: string): Promise<Ticket> {
  const { data } = await apiClient.get<Ticket>(`/tickets/${ticketId}`)
  return data
}
