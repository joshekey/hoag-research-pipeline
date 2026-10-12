/* HOAG OHIF proof-of-concept configuration ONLY.
 * Do not publish this file or enable clinical data routes until the
 * same-origin, authenticated, study-scoped DICOMweb adapter is audited.
 * This file alone is not a runnable viewer bundle.
 * OHIF 3.11 DICOMweb datasource schema.
 */
window.config = {
  routerBasename: '/ohif/',
  showStudyList: false,
  extensions: [],
  modes: [],
  dataSources: [{
    namespace: '@ohif/extension-default.dataSourcesModule.dicomweb',
    sourceName: 'hoag-restricted-dicomweb',
    configuration: {
      friendlyName: 'HOAG controlled OHIF pilot',
      name: 'HOAG-PILOT',
      qidoRoot: '/ohif-pilot/dicomweb',
      wadoRoot: '/ohif-pilot/dicomweb',
      wadoUriRoot: '/ohif-pilot/wado',
      qidoSupportsIncludeField: false,
      supportsFuzzyMatching: false,
      supportsWildcard: false,
      supportsReject: false,
      dicomUploadEnabled: false,
      enableStudyLazyLoad: true,
      imageRendering: 'wadors',
      thumbnailRendering: 'wadors'
    }
  }],
  defaultDataSourceName: 'hoag-restricted-dicomweb'
};
