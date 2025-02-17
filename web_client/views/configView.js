import View from 'girder/views/View';

import PluginConfigBreadcrumbWidget from 'girder/views/widgets/PluginConfigBreadcrumbWidget';
import { restRequest } from 'girder/rest';
import events from 'girder/events';

import ConfigViewTemplate from '../templates/largeImageConfig.pug';
import '../stylesheets/largeImageConfig.styl';

/**
 * Show the default quota settings for users and collections.
 */
var ConfigView = View.extend({
    events: {
        'submit #g-large-image-form': function (event) {
            event.preventDefault();
            this.$('#g-large-image-error-message').empty();
            this._saveSettings([{
                key: 'large_image.show_thumbnails',
                value: this.$('.g-large-image-thumbnails-show').prop('checked')
            }, {
                key: 'large_image.show_viewer',
                value: this.$('.g-large-image-viewer-show').prop('checked')
            }, {
                key: 'large_image.default_viewer',
                value: this.$('.g-large-image-default-viewer').val()
            }, {
                key: 'large_image.auto_set',
                value: this.$('.g-large-image-auto-set-on').prop('checked')
            }, {
                key: 'large_image.max_thumbnail_files',
                value: +this.$('.g-large-image-max-thumbnail-files').val()
            }, {
                key: 'large_image.max_small_image_size',
                value: +this.$('.g-large-image-max-small-image-size').val()
            }, {
                key: 'large_image.show_extra_public',
                value: this.$('.g-large-image-show-extra-public').val()
            }, {
                key: 'large_image.show_extra',
                value: this.$('.g-large-image-show-extra').val()
            }, {
                key: 'large_image.show_extra_admin',
                value: this.$('.g-large-image-show-extra-admin').val()
            }, {
                key: 'large_image.annotation_history',
                value: this.$('.g-large-image-annotation-history-show').prop('checked')
            }]);
        }
    },
    initialize: function () {
        ConfigView.getSettings((settings) => {
            this.settings = settings;
            this.render();
        });
    },

    render: function () {
        this.$el.html(ConfigViewTemplate({
            settings: this.settings,
            viewers: ConfigView.viewers
        }));
        if (!this.breadcrumb) {
            this.breadcrumb = new PluginConfigBreadcrumbWidget({
                pluginName: 'Large image',
                el: this.$('.g-config-breadcrumb-container'),
                parentView: this
            }).render();
        }

        return this;
    },

    _saveSettings: function (settings) {
        /* Now save the settings */
        return restRequest({
            type: 'PUT',
            url: 'system/setting',
            data: {
                list: JSON.stringify(settings)
            },
            error: null
        }).done(() => {
            /* Clear the settings that may have been loaded. */
            ConfigView.clearSettings();
            events.trigger('g:alert', {
                icon: 'ok',
                text: 'Settings saved.',
                type: 'success',
                timeout: 4000
            });
        }).fail((resp) => {
            this.$('#g-large-image-error-message').text(
                resp.responseJSON.message
            );
        });
    }
}, {
    /* Class methods and objects */

    /* The list of viewers is added as a property to the select widget view so
     * that it is also available to the settings page. */
    viewers: [
        {
            name: 'openseadragon',
            label: 'OpenSeaDragon',
            type: 'openseadragon'
        }, {
            name: 'openlayers',
            label: 'OpenLayers',
            type: 'openlayers'
        }, {
            name: 'leaflet',
            label: 'Leaflet',
            type: 'leaflet'
        }, {
            name: 'geojs',
            label: 'GeoJS',
            type: 'geojs'
        }, {
            name: 'slideatlas',
            label: 'SlideAtlas',
            type: 'slideatlas'
        }
    ],

    /**
     * Get settings if we haven't yet done so.  Either way, call a callback
     * when we have settings.
     *
     * @param {function} callback a function to call after the settings are
     *      fetched.  If the settings are already present, this is called
     *      without any delay.
     */
    getSettings: function (callback) {
        if (!ConfigView.settings) {
            restRequest({
                type: 'GET',
                url: 'large_image/settings'
            }).done((resp) => {
                ConfigView.settings = resp;
                if (callback) {
                    callback(ConfigView.settings);
                }
            });
        } else {
            if (callback) {
                callback(ConfigView.settings);
            }
        }
    },

    /**
     * Clear the settings so that getSettings will refetch them.
     */
    clearSettings: function () {
        delete ConfigView.settings;
    }
});

export default ConfigView;
