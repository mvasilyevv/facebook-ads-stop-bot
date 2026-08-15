import { describe, expect, it } from "vitest";

import { NAV_GROUPS, sectionForPath } from "@/lib/navigation";

/** Все пункты меню, включая вложенные, вместе с номером своего раздела. */
function menuItems(): Array<{ to: string; label: string; num: string; name: string }> {
  return NAV_GROUPS.flatMap((group) =>
    group.items.flatMap((item) =>
      [item, ...(item.children ?? [])].map((entry) => ({
        to: entry.to,
        label: entry.label,
        num: group.num,
        name: group.name,
      })),
    ),
  );
}

describe("разделы операторского интерфейса", () => {
  it("надзаголовок каждого экрана несёт номер своего раздела в меню", () => {
    for (const item of menuItems()) {
      const placement = sectionForPath(item.to);

      expect(placement, `нет раздела для ${item.to}`).not.toBeNull();
      expect(placement!.num, `номер разошёлся с меню на ${item.to}`).toBe(item.num);
      expect(placement!.name).toBe(item.name);
    }
  });

  it("номер раздела не повторяется у разных разделов", () => {
    const numbers = NAV_GROUPS.map((group) => group.num);

    expect(new Set(numbers).size).toBe(numbers.length);
  });

  it("не пускает внутренние английские слова в словарь оператора", () => {
    const forbidden = ["OPERATE", "PERFORMANCE", "CATALOG", "SYSTEM", "REMOTE", "OVERVIEW"];
    const vocabulary = [
      ...NAV_GROUPS.map((group) => group.name),
      ...menuItems().map((item) => item.label),
      ...["/incidents/1", "/cabinets/1", "/campaigns/presets", "/ads/AD1", "/offers/GH"].map(
        (path) => sectionForPath(path)?.crumb ?? "",
      ),
    ]
      .join(" ")
      .toUpperCase();

    for (const word of forbidden) {
      expect(vocabulary, `${word} в тексте для оператора`).not.toContain(word);
    }
  });

  it("на экране самого пункта меню крошка не повторяет его заголовок", () => {
    expect(sectionForPath("/ads")).toEqual({ num: "02", name: "РЕКЛАМА" });
    expect(sectionForPath("/analytics")).toEqual({ num: "03", name: "АНАЛИТИКА" });
  });

  it("вложенный экран показывает, откуда он пришёл", () => {
    expect(sectionForPath("/ads/AD1_HT_001")).toEqual({
      num: "02",
      name: "РЕКЛАМА",
      crumb: "ОБЪЯВЛЕНИЯ",
    });
    expect(sectionForPath("/offers/GH_AVI")).toEqual({
      num: "02",
      name: "РЕКЛАМА",
      crumb: "ОФФЕРЫ",
    });
    expect(sectionForPath("/actions/1842")).toEqual({
      num: "01",
      name: "ОПЕРАЦИИ",
      crumb: "ДЕЙСТВИЯ",
    });
  });

  it("считает создание кампании продолжением кампаний, а не отдельным разделом", () => {
    expect(sectionForPath("/campaigns/create")).toEqual({ num: "02", name: "РЕКЛАМА" });
    expect(sectionForPath("/campaigns/presets")).toEqual({
      num: "02",
      name: "РЕКЛАМА",
      crumb: "КАМПАНИИ",
    });
  });

  it("держит раздел за экранами, до которых добираются по ссылке из данных", () => {
    expect(sectionForPath("/incidents")).toEqual({ num: "01", name: "ОПЕРАЦИИ" });
    expect(sectionForPath("/incidents/42")).toEqual({
      num: "01",
      name: "ОПЕРАЦИИ",
      crumb: "ИНЦИДЕНТЫ",
    });
  });

  it("не выдумывает раздел для чужого пути", () => {
    expect(sectionForPath("/unknown-route")).toBeNull();
  });
});
