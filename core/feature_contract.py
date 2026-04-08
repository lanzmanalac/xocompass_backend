import pandas as pd

class FeatureContract:
    def build_exog(self, date_index: pd.DatetimeIndex) -> pd.DataFrame:
        """
        Phase 1 (Architect Way): Returns an empty DataFrame.
        This maintains the architectural contract without hardcoding 
        any exogenous variables before the Data Science sprint.
        """
        return pd.DataFrame(index=date_index)