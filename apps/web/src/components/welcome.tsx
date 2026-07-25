import type { FC } from "react";

export const Welcome: FC = () => (
  <div className="mb-6 flex flex-col items-center gap-2 px-4 text-center">
    <h2 className="text-2xl font-semibold text-balance sm:text-3xl">
      Demandez, l'agent ira chercher.
    </h2>
    <p className="text-muted-foreground max-w-md text-sm text-balance">
      Wikipédia, Hacker News, la météo et une calculatrice — le graphe enchaîne les outils
      nécessaires avant de répondre.
    </p>
  </div>
);
