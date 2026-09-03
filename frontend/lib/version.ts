// La versión del frontend sale de `package.json`, inyectada en build por
// `next.config.js`. Se cambia AHÍ y sólo ahí.
//
// El fallback "dev" cubre el caso de importar este módulo fuera de un build de
// Next (un test unitario, por ejemplo), donde la env no existe.
export const APP_VERSION = process.env.NEXT_PUBLIC_APP_VERSION ?? "dev";
