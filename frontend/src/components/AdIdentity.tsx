type AdIdentityProps = {
  adName: string;
  campaignName: string;
  adsetName: string;
  fbAdId: string;
  showScope?: boolean;
};

function formatIdentityValue(value: string, fallback: string): string {
  return value.trim() ? value : fallback;
}

export function AdIdentity({
  adName,
  campaignName,
  adsetName,
  fbAdId,
  showScope = true,
}: AdIdentityProps) {
  return (
    <div className="ad-identity">
      <strong className="ad-identity__title">
        {formatIdentityValue(adName, "Название объявления не найдено")}
      </strong>
      {showScope ? (
        <>
          <div className="ad-identity__line">
            <span className="ad-identity__label">Кампания</span>
            <span className="ad-identity__value">
              {formatIdentityValue(campaignName, "Кампания не определена")}
            </span>
          </div>
          <div className="ad-identity__line">
            <span className="ad-identity__label">Адсет</span>
            <span className="ad-identity__value">
              {formatIdentityValue(adsetName, "Адсет не определён")}
            </span>
          </div>
        </>
      ) : null}
      <div className="mono ad-identity__id">{fbAdId}</div>
    </div>
  );
}
