/**
 * StepIdentity — шаг 2 визарда: идентичность (act_id, page_id, pixel_id) + оффер.
 * Данные могут быть предзаполнены из пресета — редактируемы.
 */
import { useState } from "react";
import { Input, Button } from "@/components/ui";
import { Eyebrow } from "@/components/data";
import { haptic } from "@/lib/tg";
import { useWizardStore } from "./-wizardStore";

export function StepIdentity() {
  const { config, updateConfig, nextStep, prevStep } = useWizardStore();

  const [actId, setActId] = useState(config.act_id ?? "");
  const [pageId, setPageId] = useState(config.page_id ?? "");
  const [pixelId, setPixelId] = useState(config.pixel_id ?? "");
  const [offerCode, setOfferCode] = useState(config.offer_code ?? "");
  const [byerTag, setByerTag] = useState(config.byer_tag ?? "");
  const [error, setError] = useState<string | null>(null);

  function handleNext() {
    setError(null);
    if (!actId.trim()) {
      setError("Укажите ID рекламного кабинета");
      return;
    }
    if (!pageId.trim()) {
      setError("Укажите ID страницы");
      return;
    }
    if (!pixelId.trim()) {
      setError("Укажите ID пикселя");
      return;
    }
    if (!offerCode.trim()) {
      setError("Укажите код оффера");
      return;
    }
    haptic.impact("light");
    updateConfig({
      act_id: actId.trim(),
      page_id: pageId.trim(),
      pixel_id: pixelId.trim(),
      offer_code: offerCode.trim().toUpperCase(),
      byer_tag: byerTag.trim() || null,
    });
    nextStep();
  }

  return (
    <div className="flex flex-col gap-5 p-4 pb-8">
      <Eyebrow num="02">ИДЕНТИЧНОСТЬ + ОФФЕР</Eyebrow>

      <div className="flex flex-col gap-4">
        <Input
          label="ID рекламного кабинета"
          placeholder="act_1234567890"
          value={actId}
          onChange={(e) => setActId(e.target.value)}
          autoCapitalize="none"
          autoCorrect="off"
        />
        <Input
          label="ID страницы Facebook"
          placeholder="123456789"
          value={pageId}
          onChange={(e) => setPageId(e.target.value)}
          inputMode="numeric"
        />
        <Input
          label="ID пикселя"
          placeholder="987654321"
          value={pixelId}
          onChange={(e) => setPixelId(e.target.value)}
          inputMode="numeric"
        />
        <Input
          label="Код оффера"
          placeholder="GH_AVI"
          value={offerCode}
          onChange={(e) => setOfferCode(e.target.value.toUpperCase())}
          autoCapitalize="characters"
        />
        <Input
          label="Тег байера (опционально)"
          placeholder="MV"
          value={byerTag}
          onChange={(e) => setByerTag(e.target.value)}
          autoCapitalize="characters"
        />
      </div>

      {error !== null && (
        <p className="text-[12px] text-[var(--color-danger)]">{error}</p>
      )}

      <div className="flex flex-col gap-3 mt-2">
        <Button fullWidth onClick={handleNext}>
          Далее
        </Button>
        <Button variant="ghost" fullWidth onClick={() => { haptic.selection(); prevStep(); }}>
          Назад
        </Button>
      </div>
    </div>
  );
}
