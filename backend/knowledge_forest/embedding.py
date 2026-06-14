import numpy as np
import os
import pickle
import logging
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TextEmbedding:
    def __init__(self, model_path: str = "./models"):
        self.model_path = model_path
        self.vectorizer = None
        self.svd = None
        self.dimension = 512
        self.is_fitted = False
        self._init_from_disk()

    def _init_from_disk(self):
        os.makedirs(self.model_path, exist_ok=True)
        vec_path = os.path.join(self.model_path, "tfidf_vectorizer.pkl")
        svd_path = os.path.join(self.model_path, "svd_transformer.pkl")
        if os.path.exists(vec_path) and os.path.exists(svd_path):
            try:
                with open(vec_path, "rb") as f:
                    self.vectorizer = pickle.load(f)
                with open(svd_path, "rb") as f:
                    self.svd = pickle.load(f)
                self.is_fitted = True
                logger.info("[Embedding] 从磁盘加载已训练的TF-IDF+SVD模型")
            except Exception as e:
                logger.warning(f"[Embedding] 加载模型失败: {e}")

    def fit(self, texts: list):
        logger.info(f"[Embedding] 开始在 {len(texts)} 篇文档上训练TF-IDF向量化器")
        self.vectorizer = TfidfVectorizer(
            max_features=10000,
            ngram_range=(1, 2),
            analyzer='char_wb',
            min_df=2,
            max_df=0.9
        )
        tfidf_matrix = self.vectorizer.fit_transform(texts)
        n_features = tfidf_matrix.shape[1]
        logger.info(f"[Embedding] TF-IDF特征维度: {n_features}")

        svd_dim = min(self.dimension, n_features - 1)
        self.svd = TruncatedSVD(n_components=svd_dim, random_state=42)
        self.svd.fit(tfidf_matrix)
        self.dimension = svd_dim
        self.is_fitted = True
        logger.info(f"[Embedding] SVD降维到 {svd_dim} 维")

        os.makedirs(self.model_path, exist_ok=True)
        with open(os.path.join(self.model_path, "tfidf_vectorizer.pkl"), "wb") as f:
            pickle.dump(self.vectorizer, f)
        with open(os.path.join(self.model_path, "svd_transformer.pkl"), "wb") as f:
            pickle.dump(self.svd, f)
        logger.info("[Embedding] 模型已保存到磁盘")

    def encode(self, text: str) -> np.ndarray:
        if not self.is_fitted:
            emb = np.zeros(self.dimension, dtype=np.float32)
            emb[0] = 1.0
            return emb
        tfidf_vec = self.vectorizer.transform([text])
        svd_vec = self.svd.transform(tfidf_vec)
        vec = svd_vec[0].astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    def batch_encode(self, texts: list) -> np.ndarray:
        if not self.is_fitted:
            emb = np.zeros((len(texts), self.dimension), dtype=np.float32)
            emb[:, 0] = 1.0
            return emb
        tfidf_matrix = self.vectorizer.transform(texts)
        svd_matrix = self.svd.transform(tfidf_matrix)
        embeddings = svd_matrix.astype(np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1
        embeddings = embeddings / norms
        return embeddings

    def get_dimension(self) -> int:
        return self.dimension