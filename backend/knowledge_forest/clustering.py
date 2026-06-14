from sklearn.mixture import GaussianMixture
from sklearn.cluster import KMeans
import numpy as np
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class TextClustering:
    def __init__(self, max_clusters: int = 20, min_cluster_size: int = 10, prob_threshold: float = 0.3):
        self.max_clusters = max_clusters
        self.min_cluster_size = min_cluster_size
        self.prob_threshold = prob_threshold
        self.gmm_model = None

    def gmm_clustering(self, embeddings: np.ndarray) -> List[int]:
        n_samples = embeddings.shape[0]
        max_components = min(self.max_clusters, n_samples // self.min_cluster_size, 20)

        if max_components < 2:
            return [0] * n_samples

        best_bic = float('inf')
        best_model = None

        for n_components in range(2, max_components + 1):
            gmm = GaussianMixture(
                n_components=n_components,
                random_state=42,
                covariance_type='diag',
                n_init=1,
                max_iter=50,
            )
            gmm.fit(embeddings)
            bic = gmm.bic(embeddings)

            if bic < best_bic:
                best_bic = bic
                best_model = gmm

        if best_model is None:
            return [0] * n_samples

        self.gmm_model = best_model
        assignments = best_model.predict(embeddings)

        logger.info(f"[GMM] BIC={best_bic:.2f}, K={best_model.n_components}, 样本数={n_samples}")
        return assignments.tolist()

    def get_probabilities(self, embeddings: np.ndarray) -> np.ndarray:
        if self.gmm_model is None:
            return np.ones((embeddings.shape[0], 1)) / embeddings.shape[0]
        return self.gmm_model.predict_proba(embeddings)

    def kmeans_clustering(self, embeddings: np.ndarray, n_clusters: int = 5) -> List[int]:
        n_samples = embeddings.shape[0]
        if n_samples <= n_clusters:
            return list(range(n_samples))

        kmeans = KMeans(n_clusters=min(n_clusters, n_samples), random_state=42, n_init=10)
        assignments = kmeans.fit_predict(embeddings)
        return assignments.tolist()

    def cluster_documents(
        self,
        documents: List[Dict[str, Any]],
        embeddings: np.ndarray,
        method: str = "gmm",
    ) -> List[Dict[str, Any]]:
        if method == "gmm":
            assignments = self.gmm_clustering(embeddings)
            probabilities = self.get_probabilities(embeddings)
        else:
            assignments = self.kmeans_clustering(embeddings)
            probabilities = np.zeros((len(assignments), 1))
            for i, a in enumerate(assignments):
                probabilities[i, 0] = 1.0 if a == 0 else 0.0

        for i, (doc, assign) in enumerate(zip(documents, assignments)):
            probs = probabilities[i]
            soft_clusters = {
                int(cid): float(p)
                for cid, p in enumerate(probs)
                if p >= self.prob_threshold
            }
            doc["cluster_id"] = int(assign)
            doc["cluster_probabilities"] = soft_clusters

        return documents