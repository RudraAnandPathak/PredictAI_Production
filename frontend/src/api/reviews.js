// Reviews of a person's own work, and the owner's list of every review.
const id = encodeURIComponent;

export const reviewsApi = (client) => {
  const data = (p) => p.then((r) => r.data);
  return {
    mine: (kind, targetId) => data(client.request(`/reviews/${id(kind)}/${id(targetId)}`, { method: 'GET' })),
    save: (kind, targetId, rating, comment) => data(client.request(`/reviews/${id(kind)}/${id(targetId)}`, { method: 'PUT', body: { rating, comment: comment || null } })),
    remove: (kind, targetId) => data(client.request(`/reviews/${id(kind)}/${id(targetId)}`, { method: 'DELETE' })),
    ownerList: ({ kind, rating, page } = {}) => {
      const q = new URLSearchParams();
      if (kind) q.set('kind', kind);
      if (rating) q.set('rating', String(rating));
      if (page) q.set('page', String(page));
      const s = q.toString();
      return data(client.request(`/owner/reviews${s ? `?${s}` : ''}`, { method: 'GET' }));
    },
  };
};
