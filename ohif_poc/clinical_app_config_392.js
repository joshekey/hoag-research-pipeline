/* OHIF 3.9.2 — HOAG clinical single-study pilot.
 * Install as /opt/hoag-research/ohif-assets/app-config.js ONLY after
 * clinical gateway approval and single-study allowlist setup.
 * No external imaging endpoints or token prompts.
 */
window.config = {
  routerBasename: '/',
  extensions: [],
  modes: [],
  showStudyList: false,
  defaultDataSourceName: 'hoag-clinical',
  dataSources: [
    {
      namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
      sourceName: 'hoag-clinical',
      configuration: {
        friendlyName: 'HOAG Restricted Pilot',
        name: 'HOAG-RESTRICTED',
        qidoRoot: '/ohif/dicomweb',
        wadoRoot: '/ohif/dicomweb',
        wadoUriRoot: '/ohif/dicomweb',
        qidoSupportsIncludeField: false,
        supportsReject: false,
        supportsStow: false,
        supportsFuzzyMatching: false,
        supportsWildcard: false,
        imageRendering: 'wadors',
        thumbnailRendering: 'wadors',
        enableStudyLazyLoad: true,
        staticWado: false,
        omitQuotationForMultipartRequest: true
      }
    }
  ]
};
