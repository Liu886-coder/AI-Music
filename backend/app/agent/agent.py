from app.services.recommendation_service import RecommendationService
class MusicAgent:
    def __init__(self): self.service = RecommendationService()
    async def run(self, db, user_id: str, message: str, limit: int, controls=None, previous_ids=None):
        return await self.service.recommend(db, user_id, message, limit, controls, previous_ids)
